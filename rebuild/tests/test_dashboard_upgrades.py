"""
tests/test_dashboard_upgrades.py
================================
Lane G — dashboard + reports-landing upgrades.

1. Monthly-revenue chart: the router has computed a period-selectable monthly
   revenue series since Seam 4, but the template never rendered it (dead
   computation). The dashboard must now ship a chart card whose data crosses
   into JS via autoescape-safe data-* attributes, with ?period= toggle links.

2. Sales heartbeat: an "Open Quotes" KPI card counting quotes still in play
   (not converted / declined / expired — same terminal set the quotes-list
   tabs use) plus CRM follow-ups due today / overdue via
   CRMService.follow_ups_due(), and a rail card listing those follow-ups.

3. Reports landing resilience: one poisoned ReportService metric must not 500
   the whole /reports/ landing — the failed figure renders an em-dash while
   every other card still shows real numbers.
"""
from __future__ import annotations

import html
import itertools
import json
import pathlib
import re
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import QuoteStatus
from app.models.customer import Activity, Customer
from app.models.quote import Quote
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


# ── Seed helpers ───────────────────────────────────────────────────────────────

def _customer(db, name: str | None = None) -> Customer:
    c = Customer(company_name=name or f"Dash Co {next(_seq)}")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _quote(db, cust: Customer, status: str) -> Quote:
    q = Quote(
        quote_number=f"Q-TEST-{next(_seq):04d}",
        customer_id=cust.id,
        status=status,
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return q


def _followup(db, cust: Customer, due: date, notes: str = "call back") -> Activity:
    a = Activity(customer_id=cust.id, notes=notes, follow_up_date=due)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# ── Render-parsing helpers ─────────────────────────────────────────────────────

def _get_dashboard(client) -> str:
    r = client.get("/")
    assert r.status_code == 200, r.text
    return r.text


def _open_quotes_value(body: str) -> int:
    """Extract the Open Quotes stat-card value (an <a> wrapping the count)."""
    m = re.search(
        r'Open Quotes</div>\s*<div class="stat-value">\s*<a[^>]*>(\d+)</a>',
        body,
    )
    assert m, "Open Quotes card not found on dashboard"
    return int(m.group(1))


def _followups_counts(body: str) -> tuple[int, int]:
    """Return (total_due, overdue) from the Open Quotes card sub-line.

    Zero state renders the literal 'No follow-ups due'.
    """
    if "No follow-ups due" in body:
        return 0, 0
    m = re.search(r"(\d+) follow-ups? due(?: · (\d+) overdue)?", body)
    assert m, "follow-ups sub-line not found on dashboard"
    return int(m.group(1)), int(m.group(2) or 0)


def _chart_labels(body: str) -> list:
    m = re.search(r'data-labels="([^"]*)"', body)
    assert m, "revenue chart data-labels attribute missing"
    return json.loads(html.unescape(m.group(1)))


# ── 1. Monthly-revenue chart ───────────────────────────────────────────────────

def test_chart_card_present(client):
    """The chart card exists: canvas + data attrs + Chart.js init hook."""
    body = _get_dashboard(client)
    assert 'id="revenue-chart"' in body
    assert 'data-labels="' in body and 'data-totals="' in body
    # Init is deferred-safe: Chart.js loads `defer`, so the inline script must
    # wait for DOMContentLoaded rather than calling new Chart() at parse time.
    assert "DOMContentLoaded" in body
    # Period toggle links (existing query-param already computed by the router).
    for p in ("3m", "6m", "ytd", "12m"):
        assert f"?period={p}" in body
    # Card header carries the period label.
    assert "Monthly Revenue" in body


def test_chart_period_param_controls_bucket_count(client):
    body_default = _get_dashboard(client)
    assert len(_chart_labels(body_default)) == 6  # default period=6m

    r3 = client.get("/?period=3m")
    assert r3.status_code == 200
    assert len(_chart_labels(r3.text)) == 3

    r12 = client.get("/?period=12m")
    assert r12.status_code == 200
    assert len(_chart_labels(r12.text)) == 12

    # data-totals parses to numbers, one per label
    m = re.search(r'data-totals="([^"]*)"', r3.text)
    totals = json.loads(html.unescape(m.group(1)))
    assert len(totals) == 3
    assert all(isinstance(t, (int, float)) for t in totals)


# ── 2. Open Quotes + follow-ups card ──────────────────────────────────────────

def test_open_quotes_counts_pipeline_not_terminal(client, db):
    """Draft + sent count as open; converted / declined / expired do not."""
    before = _open_quotes_value(_get_dashboard(client))

    cust = _customer(db)
    _quote(db, cust, QuoteStatus.DRAFT)
    _quote(db, cust, QuoteStatus.SENT)
    _quote(db, cust, QuoteStatus.CONVERTED)
    _quote(db, cust, QuoteStatus.DECLINED)
    _quote(db, cust, QuoteStatus.EXPIRED)

    after = _open_quotes_value(_get_dashboard(client))
    assert after == before + 2  # only the two live-pipeline quotes moved it


def test_followups_due_today_and_overdue_counts(client, db):
    """CRMService.follow_ups_due drives the card: due-today + overdue split."""
    before_total, before_overdue = _followups_counts(_get_dashboard(client))

    cust = _customer(db, name="Followup Freight LLC")
    today = date.today()
    _followup(db, cust, due=today, notes="quote follow-up today")
    _followup(db, cust, due=today - timedelta(days=3), notes="way overdue call")
    # A future follow-up must NOT count.
    _followup(db, cust, due=today + timedelta(days=5), notes="next week")
    # A done follow-up must NOT count.
    done = _followup(db, cust, due=today - timedelta(days=1), notes="already handled")
    from datetime import datetime as _dt
    done.follow_up_done_at = _dt.utcnow()
    db.commit()

    body = _get_dashboard(client)
    total, overdue = _followups_counts(body)
    assert total == before_total + 2
    assert overdue == before_overdue + 1

    # Rail card lists the due follow-ups with a link to the customer activity tab.
    assert 'id="followups-due"' in body
    assert "Followup Freight LLC" in body
    assert f"/customers/{cust.id}?tab=activity" in body
    # Done button posts to the existing follow-up-done endpoint.
    assert f"/activities/{done.id}/follow-up-done" not in body  # done item excluded


# ── 3. Reports landing resilience ─────────────────────────────────────────────

def test_reports_landing_baseline_renders(client):
    r = client.get("/reports/")
    assert r.status_code == 200
    assert "Some snapshot figures could not be loaded" not in r.text


def test_reports_landing_survives_poisoned_ar_metric(client, monkeypatch):
    """One raising metric renders an em-dash for that card only — no 500."""
    from app.services.report_service import ReportService

    def boom(self, *a, **k):
        raise RuntimeError("poisoned metric")

    monkeypatch.setattr(ReportService, "get_ar_aging", boom)
    r = client.get("/reports/")
    assert r.status_code == 200
    body = r.text

    # Failed metric is named in the soft banner and renders an em-dash.
    assert "Some snapshot figures could not be loaded" in body
    assert "ar_aging" in body
    m = re.search(r'A/R Total</p>\s*<p class="stat-value[^"]*">\s*([^<]+?)\s*</p>', body)
    assert m, "A/R Total card missing"
    assert "—" in m.group(1)  # em-dash, not a fake $0.00
    assert "$" not in m.group(1)

    # Every other card still renders a real figure.
    m2 = re.search(r'Inventory Value</p>\s*<p class="stat-value[^"]*">\s*([^<]+?)\s*</p>', body)
    assert m2 and "$" in m2.group(1)


def test_reports_landing_survives_poisoned_dead_stock(client, monkeypatch):
    """Per-metric isolation: a different raising metric also degrades softly."""
    from app.services.report_service import ReportService

    def boom(self, *a, **k):
        raise RuntimeError("poisoned metric")

    monkeypatch.setattr(ReportService, "get_dead_stock", boom)
    r = client.get("/reports/")
    assert r.status_code == 200
    assert "dead_stock" in r.text
    # AR card unaffected this time.
    m = re.search(r'A/R Total</p>\s*<p class="stat-value[^"]*">\s*([^<]+?)\s*</p>', r.text)
    assert m and "$" in m.group(1)
