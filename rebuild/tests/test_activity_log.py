"""
tests/test_activity_log.py
===========================
Verification suite for the unified Activity Log (ACTIVITY_LOG_CONTRACT.md §5).

GATED ON BACKEND LANDING
─────────────────────────
These tests auto-skip until the Backend lane commits the activity log contract
(adds the 5 new columns to `customer_call_logs` + implements `log_activity`,
`follow_ups_due`, `mark_follow_up_done`, and `get_timeline` in CRMService +
registers the POST /activities route).  The skip fixture checks for the
`activity_type` column on CustomerCallLog; once that column exists, every test
runs.

When Backend lands:
  1. The skip is automatically lifted.
  2. Run:  .venv\\Scripts\\python.exe -m pytest tests/test_activity_log.py -v
  3. Fix any failures, then commit alongside the Backend change.

Contract reference: ACTIVITY_LOG_CONTRACT.md
─────────────────────────────────────────────
§1 — extend customer_call_logs: activity_type, follow_up_date,
     follow_up_done_at, related_entity_type, related_entity_id.
§2 — CRMService: log_activity(), follow_ups_due(), mark_follow_up_done(),
     get_timeline(), activities_for_entity().
§4 — logged_by_id = signed-in user (O2 attribution seam).
§5 — what QA verifies (this file).
"""
from __future__ import annotations

import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1

_counter = __import__("itertools").count(1)


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


# ── Gate ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _require_activity_log(db):
    """Skip every test until Backend lands the activity_type column."""
    from app.models.customer import CustomerCallLog
    if not hasattr(CustomerCallLog, "activity_type"):
        pytest.skip(
            "GATED: CustomerCallLog.activity_type not yet present — "
            "awaiting Backend landing of ACTIVITY_LOG_CONTRACT.md"
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"Activity Co {next(_counter)}",
                 contact_name="QA", email=f"act{next(_counter)}@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


# ── §5 tests ─────────────────────────────────────────────────────────────────

def test_log_each_activity_type_persists(db):
    """log_activity() persists a row for each ActivityType value."""
    from app.constants import ActivityType
    from app.models.customer import CustomerCallLog
    from app.services.crm_service import CRMService

    cust = _customer(db)
    svc = CRMService(db, _UID)

    for atype in ActivityType:
        svc.log_activity(cust.id, activity_type=atype, notes=f"Test {atype}")

    rows = (
        db.query(CustomerCallLog)
        .filter(CustomerCallLog.customer_id == cust.id)
        .all()
    )
    logged_types = {r.activity_type for r in rows}
    for atype in ActivityType:
        assert str(atype) in logged_types, f"activity_type={atype!r} was not persisted"


def test_follow_ups_due_returns_open_and_overdue_only(db):
    """follow_ups_due() returns rows with follow_up_date <= today and not done."""
    from app.services.crm_service import CRMService

    cust = _customer(db)
    svc = CRMService(db, _UID)

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    tomorrow = today + datetime.timedelta(days=1)

    # Overdue
    svc.log_activity(cust.id, activity_type="call", notes="overdue",
                     follow_up_date=yesterday)
    # Due today
    svc.log_activity(cust.id, activity_type="call", notes="due today",
                     follow_up_date=today)
    # Future — must NOT appear
    svc.log_activity(cust.id, activity_type="call", notes="future",
                     follow_up_date=tomorrow)

    due = svc.follow_ups_due(through=today)
    due_notes = {r.notes for r in due if r.customer_id == cust.id}
    assert "overdue" in due_notes
    assert "due today" in due_notes
    assert "future" not in due_notes


def test_mark_follow_up_done_stamps_and_removes_from_due(db):
    """mark_follow_up_done stamps follow_up_done_at and drops from due list."""
    from app.services.crm_service import CRMService

    cust = _customer(db)
    svc = CRMService(db, _UID)
    today = datetime.date.today()

    svc.log_activity(cust.id, activity_type="call", notes="to close",
                     follow_up_date=today)

    due_before = [r for r in svc.follow_ups_due(through=today)
                  if r.customer_id == cust.id and r.notes == "to close"]
    assert len(due_before) == 1

    svc.mark_follow_up_done(due_before[0].id)
    db.expire_all()

    due_after = [r for r in svc.follow_ups_due(through=today)
                 if r.customer_id == cust.id and r.notes == "to close"]
    assert len(due_after) == 0, "Done follow-up must not appear in due list"

    from app.models.customer import CustomerCallLog
    row = db.query(CustomerCallLog).filter(CustomerCallLog.id == due_before[0].id).first()
    assert row.follow_up_done_at is not None


def test_get_timeline_merges_both_stores_newest_first(db):
    """get_timeline merges customer_call_logs + communication_log newest-first."""
    from app.services.crm_service import CRMService

    cust = _customer(db)
    svc = CRMService(db, _UID)

    svc.log_activity(cust.id, activity_type="call", notes="first")
    svc.log_activity(cust.id, activity_type="note", notes="second")

    timeline = svc.get_timeline(cust.id, limit=50)
    assert len(timeline) >= 2

    # Newest must come first
    manual_entries = [t for t in timeline if t.get("kind") == "activity"]
    assert len(manual_entries) >= 2

    notes = [e["note"] for e in manual_entries]
    assert notes.index("second") < notes.index("first"), (
        "get_timeline must return entries newest-first"
    )


def test_activities_for_entity_returns_doc_activities(db):
    """activities_for_entity returns activities linked to a given document."""
    from app.constants import QuoteStatus
    from app.models.quote import Quote
    from app.services.crm_service import CRMService

    cust = _customer(db)
    quote = Quote(quote_number=f"Q-ACT-{next(_counter)}",
                  customer_id=cust.id, status=QuoteStatus.DRAFT)
    db.add(quote); db.commit(); db.refresh(quote)

    svc = CRMService(db, _UID)
    svc.log_activity(cust.id, activity_type="call", notes="quote call",
                     related_entity_type="quote", related_entity_id=quote.id)
    svc.log_activity(cust.id, activity_type="note", notes="unlinked note")

    doc_activities = svc.activities_for_entity("quote", quote.id)
    notes = [a.notes for a in doc_activities]
    assert "quote call" in notes
    assert "unlinked note" not in notes


def test_log_activity_attribution_uses_signed_in_user(db):
    """logged_by_id must be the signed-in user (O2 attribution — §4)."""
    from app.auth import hash_password, make_session_token, SESSION_COOKIE
    from app.constants import UserRole
    from app.models.customer import CustomerCallLog
    from app.models.user import User
    from app.services.crm_service import CRMService

    cust = _customer(db)

    # Seed a distinct user
    agent = User(name="Agent", username=f"agent{next(_counter)}",
                 password_hash=hash_password("pw"), role=UserRole.SALES,
                 is_active=True)
    db.add(agent); db.commit(); db.refresh(agent)
    agent_id = agent.id

    # Log activity AS that user via the service (direct attribution)
    svc = CRMService(db, agent_id)
    svc.log_activity(cust.id, activity_type="call", notes="attributed")
    db.expire_all()

    row = (
        db.query(CustomerCallLog)
        .filter(CustomerCallLog.customer_id == cust.id,
                CustomerCallLog.notes == "attributed")
        .first()
    )
    assert row is not None
    assert row.logged_by_id == agent_id, (
        f"logged_by_id should be {agent_id} (the agent); got {row.logged_by_id}"
    )


# ── Route smoke ───────────────────────────────────────────────────────────────

def test_post_activities_global_persists(client, db):
    """POST /activities (global — no document link) persists the activity."""
    from app.models.customer import CustomerCallLog

    cust = _customer(db)
    r = client.post("/activities", data={
        "customer_id": str(cust.id),
        "activity_type": "note",
        "notes": "global note smoke",
    }, follow_redirects=False)
    assert r.status_code in (200, 201, 303), r.text[:300]

    row = (
        db.query(CustomerCallLog)
        .filter(CustomerCallLog.customer_id == cust.id,
                CustomerCallLog.notes == "global note smoke")
        .first()
    )
    assert row is not None, "POST /activities must persist the activity row"
    assert row.related_entity_type is None


def test_post_activities_from_quote_sets_related_entity(client, db):
    """POST /activities with related doc sets related_entity_type + _id."""
    from app.constants import QuoteStatus
    from app.models.customer import CustomerCallLog
    from app.models.quote import Quote

    cust = _customer(db)
    quote = Quote(quote_number=f"Q-RSMK-{next(_counter)}",
                  customer_id=cust.id, status=QuoteStatus.DRAFT)
    db.add(quote); db.commit(); db.refresh(quote)

    r = client.post("/activities", data={
        "customer_id": str(cust.id),
        "activity_type": "call",
        "notes": "quote smoke",
        "related_entity_type": "quote",
        "related_entity_id": str(quote.id),
    }, follow_redirects=False)
    assert r.status_code in (200, 201, 303), r.text[:300]

    row = (
        db.query(CustomerCallLog)
        .filter(CustomerCallLog.customer_id == cust.id,
                CustomerCallLog.notes == "quote smoke")
        .first()
    )
    assert row is not None
    assert row.related_entity_type == "quote"
    assert row.related_entity_id == quote.id
