"""
tests/test_customer_quote_badge.py
==================================
Phase-1.1 regression — the customer-detail "Quotes (N)" tab badge.

THE INVARIANT
-------------
The badge number must equal the count of OPEN quotes the panel actually shows,
and "open" must mean the same thing everywhere: NOT a terminal status. The
router already defines the canonical terminal set:

    app/routers/customers.py
    _CLOSED_QUOTE_STATUSES = [CONVERTED, DECLINED, EXPIRED]

and the customer *preview* panel's ``open_quote_count`` correctly excludes all
three. The detail page renders ``open_quotes|length`` for BOTH the tab badge and
the quotes table, so whatever leaks into ``open_quotes`` inflates both together.

THE BUG (pinned by test_quote_badge_excludes_expired, xfail)
------------------------------------------------------------
``customer_detail()`` builds ``open_quotes`` excluding only
``[CONVERTED, DECLINED]`` — it omits ``EXPIRED``. So an expired quote is counted
as open and rendered in the "Open quotes" panel. Fix: have ``customer_detail``
use ``_CLOSED_QUOTE_STATUSES`` (add EXPIRED) like the preview panel. When that
lands, drop the xfail marker and this becomes a hard guard.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_customer_quote_badge.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import QuoteStatus
from app.main import app
from app.models.customer import Customer
from app.models.quote import Quote
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)

# Open (non-terminal) quote statuses — what the badge SHOULD count.
_OPEN_STATUSES = [QuoteStatus.DRAFT, QuoteStatus.SENT, QuoteStatus.ACCEPTED]


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


# ── Helpers ──────────────────────────────────────────────────────────────────

def _customer(db) -> Customer:
    c = Customer(company_name=f"Badge Co {next(_seq)}", contact_name="QA",
                 email="badge@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _quote(db, customer_id: int, status: str) -> Quote:
    q = Quote(quote_number=f"Q-BADGE-{next(_seq):04d}",
              customer_id=customer_id, status=status)
    db.add(q); db.commit(); db.refresh(q)
    return q


def _badge_count(html: str) -> int:
    """The integer in the Quotes tab badge, or 0 when no badge renders
    (the template hides the span when there are no open quotes)."""
    m = re.search(r"tab = 'quotes'.*?badge-blue\">\s*(\d+)\s*<", html, re.DOTALL)
    return int(m.group(1)) if m else 0


def _panel_quote_ids(html: str) -> set[int]:
    """Quote ids whose detail link (/quotes/<id>) is rendered INSIDE THE QUOTES
    TAB PANEL. Scoped to that panel on purpose: the unified activity Timeline tab
    (#8) also links every quote chronologically (including terminal ones), so a
    whole-page scan would conflate the two. The badge invariant is strictly about
    the quotes panel, so that's what we measure."""
    m = re.search("x-show=\"tab === 'quotes'\"(.*?)(?:x-show=\"tab ===|\\Z)",
                  html, re.DOTALL)
    scope = m.group(1) if m else html
    return {int(x) for x in re.findall(r"/quotes/(\d+)\b", scope)}


# ── 1. Badge number == the open-quote rows the panel shows ────────────────────

def test_quote_badge_equals_panel_row_count(client, db):
    """Literal invariant: the 'Quotes (N)' badge counts exactly the quotes the
    panel displays — they read the same list, so they can never disagree."""
    cust = _customer(db)
    open_ids = {_quote(db, cust.id, st).id for st in (QuoteStatus.DRAFT, QuoteStatus.SENT)}

    html = client.get(f"/customers/{cust.id}").text
    shown = _panel_quote_ids(html)

    assert shown == open_ids
    assert _badge_count(html) == len(open_ids) == 2


# ── 2. Terminal statuses (converted, declined) are excluded ───────────────────

def test_quote_badge_excludes_converted_and_declined(client, db):
    cust = _customer(db)
    open_q = _quote(db, cust.id, QuoteStatus.DRAFT)
    conv_q = _quote(db, cust.id, QuoteStatus.CONVERTED)
    decl_q = _quote(db, cust.id, QuoteStatus.DECLINED)

    html = client.get(f"/customers/{cust.id}").text
    shown = _panel_quote_ids(html)

    assert open_q.id in shown
    assert conv_q.id not in shown, "converted quote must not count as open"
    assert decl_q.id not in shown, "declined quote must not count as open"
    assert _badge_count(html) == 1


# ── 3. EXPIRED must be excluded too — the Phase-1.1 defect (FIXED) ─────────────
# customer_detail() now builds open_quotes with _CLOSED_QUOTE_STATUSES (incl.
# EXPIRED), matching the list/preview paths. xfail marker dropped → hard guard.

def test_quote_badge_excludes_expired(client, db):
    cust = _customer(db)
    open_ids = {_quote(db, cust.id, st).id for st in _OPEN_STATUSES}  # 3 open
    expired_q = _quote(db, cust.id, QuoteStatus.EXPIRED)

    html = client.get(f"/customers/{cust.id}").text
    shown = _panel_quote_ids(html)

    assert expired_q.id not in shown, "expired quote must not count as open"
    assert shown == open_ids
    assert _badge_count(html) == len(open_ids) == 3
