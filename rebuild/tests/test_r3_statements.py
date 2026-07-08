"""
tests/test_r3_statements.py
===========================
R3 — customer statements are PERSISTED at the print/PDF ("this went to the
customer") moment, so a disputed statement can be re-rendered from its stored
snapshot even after the underlying invoices/payments change.

Covers:
  1. /statement/print persists a CustomerStatement row with correct aging
     buckets (incl. the NEW over_90 terminal column — due_120 is dead) and a
     JSON-safe snapshot of the full line data.
  2. Idempotency: same customer + same period regenerated the SAME day reuses
     the row (no duplicate statement numbers).
  3. A different period mints a NEW statement number.
  4. The archived view renders from snapshot_json — numbers unchanged even
     after the underlying invoice is mutated (dispute-resolution guarantee).
  5. Statement numbers are sequential (ST-YYYY-NNNN counter).
  6. Regression: live generate_statement output is unchanged by persistence,
     and the print view still renders payment references (Suite-B B-5 guard).
  7. The statement form page lists history with an archive link.

Fixture pattern mirrors tests/test_r1_core_money.py: module-isolated
in-memory engine + TestClient startup. Never touches jaks.db.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()
_counter = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"R3 Stmt Cust {next(_counter)}", is_active=True)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer, *, amount: float, due_days_ago: int | None = None):
    """OPEN invoice created now with one non-taxable line totalling `amount`.

    due_days_ago: positive = overdue by N days, negative = due N days in the
    future, None = no due date (aging treats it as current).
    """
    from app.constants import InvoiceStatus
    from app.models.invoice import Invoice, InvoiceLine
    due = None
    if due_days_ago is not None:
        due = dt.datetime.utcnow() - dt.timedelta(days=due_days_ago)
    inv = Invoice(
        invoice_number=f"INV-R3ST-{next(_counter):04d}",
        customer_id=customer.id,
        status=InvoiceStatus.OPEN,
        due_date=due,
    )
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, qty=1, unit_price=amount, is_taxable=False))
    db.commit(); db.refresh(inv)
    return inv


def _window() -> tuple[str, str]:
    """Period straddling 'today' in both local AND UTC time so invoices
    created now (UTC server_default) are always in-period."""
    today = dt.date.today()
    return (
        (today - dt.timedelta(days=1)).isoformat(),
        (today + dt.timedelta(days=1)).isoformat(),
    )


def _rows_for(db, customer):
    from app.models.statement import CustomerStatement
    db.expire_all()
    return (
        db.query(CustomerStatement)
        .filter(CustomerStatement.customer_id == customer.id)
        .order_by(CustomerStatement.id)
        .all()
    )


# ---------------------------------------------------------------------------
# 1. Print persists a row — buckets (incl. over_90) + snapshot
# ---------------------------------------------------------------------------

def test_print_persists_row_with_buckets_and_snapshot(db, client):
    cust = _customer(db)
    _open_invoice(db, cust, amount=100.0, due_days_ago=-30)   # not yet due → current
    _open_invoice(db, cust, amount=200.0, due_days_ago=45)    # 31_60 → due_60
    _open_invoice(db, cust, amount=300.0, due_days_ago=120)   # over_90 (terminal)
    start, end = _window()

    r = client.get(f"/customers/{cust.id}/statement/print?start={start}&end={end}")
    assert r.status_code == 200

    rows = _rows_for(db, cust)
    assert len(rows) == 1, "printing must persist exactly one statement row"
    row = rows[0]

    assert row.statement_number.startswith("ST-")
    assert row.date_range_start.isoformat() == start
    assert row.date_range_end.isoformat() == end
    assert row.opening_balance == 0.0
    assert row.total_invoiced == 600.0
    assert row.closing_balance == 600.0

    # Aging buckets follow ar_aging_utils keys
    assert row.current_due == 100.0
    assert row.due_30 == 0.0
    assert row.due_60 == 200.0
    assert row.due_90 == 0.0
    assert row.over_90 == 300.0, "terminal bucket must land in the NEW over_90 column"
    assert row.due_120 == 0.0, "due_120 is a dead legacy column — never written"

    # Snapshot is full, JSON-safe line data
    snap = json.loads(row.snapshot_json)
    assert snap["period_start"] == start and snap["period_end"] == end
    assert snap["closing_balance"] == 600.0
    assert snap["aging"]["over_90"] == 300.0
    assert len(snap["lines"]) == 3
    assert all(isinstance(ln["txn_date"], str) for ln in snap["lines"])
    assert snap["customer"]["company_name"] == cust.company_name

    # The live print view shows the minted number (so mailed copies carry it)
    assert row.statement_number in r.text


# ---------------------------------------------------------------------------
# 2. Same-day regenerate (same customer + period) reuses the row
# ---------------------------------------------------------------------------

def test_same_day_regenerate_does_not_duplicate(db, client):
    cust = _customer(db)
    _open_invoice(db, cust, amount=150.0, due_days_ago=10)
    start, end = _window()
    url = f"/customers/{cust.id}/statement/print?start={start}&end={end}"

    assert client.get(url).status_code == 200
    first = _rows_for(db, cust)
    assert len(first) == 1
    number = first[0].statement_number

    assert client.get(url).status_code == 200
    rows = _rows_for(db, cust)
    assert len(rows) == 1, "same customer+period+day must reuse the existing row"
    assert rows[0].statement_number == number, "no new number minted on re-print"


# ---------------------------------------------------------------------------
# 3. Different period mints a new statement number
# ---------------------------------------------------------------------------

def test_different_period_mints_new_number(db, client):
    cust = _customer(db)
    _open_invoice(db, cust, amount=75.0, due_days_ago=5)
    start, end = _window()
    wider_start = (dt.date.today() - dt.timedelta(days=10)).isoformat()

    assert client.get(
        f"/customers/{cust.id}/statement/print?start={start}&end={end}"
    ).status_code == 200
    assert client.get(
        f"/customers/{cust.id}/statement/print?start={wider_start}&end={end}"
    ).status_code == 200

    rows = _rows_for(db, cust)
    assert len(rows) == 2, "a different period is a different statement"
    assert rows[0].statement_number != rows[1].statement_number
    assert {r.date_range_start.isoformat() for r in rows} == {start, wider_start}


# ---------------------------------------------------------------------------
# 4. Archived view is immutable — mutate the invoice, archive doesn't move
# ---------------------------------------------------------------------------

def test_archived_view_renders_from_snapshot_after_data_changes(db, client):
    from app.models.invoice import InvoiceLine
    from app.models.statement import CustomerStatement
    from app.services.statement_service import StatementService

    cust = _customer(db)
    inv = _open_invoice(db, cust, amount=100.0, due_days_ago=45)
    start, end = _window()

    assert client.get(
        f"/customers/{cust.id}/statement/print?start={start}&end={end}"
    ).status_code == 200
    row = _rows_for(db, cust)[0]
    assert row.closing_balance == 100.0

    # ── Mutate the underlying invoice (the dispute scenario) ──
    line = db.query(InvoiceLine).filter(InvoiceLine.invoice_id == inv.id).one()
    line.unit_price = 999.0
    db.commit()

    # Live regeneration now reads the changed data...
    live = StatementService(db).generate_statement(
        cust.id, dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    )
    assert live["closing_balance"] == 999.0

    # ...but the ARCHIVED copy still shows exactly what was sent.
    r = client.get(f"/customers/{cust.id}/statement/archive/{row.id}")
    assert r.status_code == 200
    assert "ARCHIVED COPY" in r.text, "archive view must be clearly labelled"
    assert row.statement_number in r.text
    assert "$100.00" in r.text, "archived numbers must come from the snapshot"
    assert "$999.00" not in r.text, "archive must NOT reflect later mutations"

    # The stored row itself is untouched by viewing / live regeneration.
    db.expire_all()
    stored = db.get(CustomerStatement, row.id)
    assert stored.closing_balance == 100.0
    assert json.loads(stored.snapshot_json)["closing_balance"] == 100.0

    # get_statement(statement_id) round-trips the parsed snapshot for re-print.
    parsed = StatementService(db).get_statement(row.id)
    assert parsed is not None
    assert parsed["closing_balance"] == 100.0
    assert parsed["period_start"] == dt.date.fromisoformat(start)
    assert [ln["running_balance"] for ln in parsed["lines"]] == [100.0]
    assert StatementService(db).get_statement(999_999) is None


# ---------------------------------------------------------------------------
# 5. Statement numbers are sequential
# ---------------------------------------------------------------------------

def test_statement_numbers_sequential(db, client):
    c1 = _customer(db)
    c2 = _customer(db)
    _open_invoice(db, c1, amount=10.0)
    _open_invoice(db, c2, amount=20.0)
    start, end = _window()

    assert client.get(
        f"/customers/{c1.id}/statement/print?start={start}&end={end}"
    ).status_code == 200
    assert client.get(
        f"/customers/{c2.id}/statement/print?start={start}&end={end}"
    ).status_code == 200

    n1 = _rows_for(db, c1)[0].statement_number
    n2 = _rows_for(db, c2)[0].statement_number
    year = str(dt.date.today().year)
    assert n1.startswith(f"ST-{year}-") and n2.startswith(f"ST-{year}-")
    assert int(n2.rsplit("-", 1)[1]) == int(n1.rsplit("-", 1)[1]) + 1


# ---------------------------------------------------------------------------
# 5b. Sacred numbers — a failed persist that rolls back burns NO number
# ---------------------------------------------------------------------------

def test_failed_persist_rollback_does_not_burn_a_number(db, client, monkeypatch):
    """bump_counter flushes (never commits) so the increment lives in the
    caller's transaction: if persistence fails after the number was allocated,
    rollback returns the number to the pool — no gaps in ST-YYYY-NNNN."""
    from app.services.statement_service import StatementService

    cust = _customer(db)
    _open_invoice(db, cust, amount=40.0, due_days_ago=3)
    start, end = _window()
    ps, pe = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    svc = StatementService(db)

    # Anchor the sequence with one successful persist.
    anchor = svc.persist_statement(svc.generate_statement(cust.id, ps, pe))
    anchor_n = int(anchor.statement_number.rsplit("-", 1)[1])

    # Force a failure AFTER bump_counter has allocated the next number
    # (snapshot serialization is the last step before commit).
    def boom(_stmt):
        raise RuntimeError("simulated failure after number allocation")
    monkeypatch.setattr(StatementService, "_snapshot_payload", staticmethod(boom))

    wider_ps = ps - dt.timedelta(days=5)   # different period → new-number path
    failed_stmt = svc.generate_statement(cust.id, wider_ps, pe)
    with pytest.raises(RuntimeError):
        svc.persist_statement(failed_stmt)
    db.rollback()
    monkeypatch.undo()

    # The failed attempt left no row behind...
    assert len(_rows_for(db, cust)) == 1

    # ...and the very next successful persist gets anchor+1 — the allocation
    # made inside the failed transaction was rolled back, not burned.
    retry = svc.persist_statement(svc.generate_statement(cust.id, wider_ps, pe))
    assert int(retry.statement_number.rsplit("-", 1)[1]) == anchor_n + 1


# ---------------------------------------------------------------------------
# 6. Regression — live statement output unchanged by persistence
# ---------------------------------------------------------------------------

def test_live_statement_output_unchanged_by_persistence(db, client):
    from app.constants import PaymentDirection, PaymentMethod
    from app.models.invoice import Payment
    from app.services.statement_service import StatementService

    cust = _customer(db)
    _open_invoice(db, cust, amount=250.0, due_days_ago=15)
    pmt = Payment(
        customer_id=cust.id,
        amount_received=50.0,
        payment_method=PaymentMethod.CHECK,
        check_number="CHK-R3-1",
        direction=PaymentDirection.INCOMING_FROM_CUSTOMER,
        payment_date=dt.datetime.utcnow(),
    )
    db.add(pmt); db.commit()

    start, end = _window()
    ps, pe = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    svc = StatementService(db)
    before = svc.generate_statement(cust.id, ps, pe)

    # Print — persists a row as a side effect.
    r = client.get(f"/customers/{cust.id}/statement/print?start={start}&end={end}")
    assert r.status_code == 200
    assert "CHK-R3-1" in r.text, "payment reference must still render (B-5 guard)"
    assert "ARCHIVED COPY" not in r.text, "live print view is not the archive view"

    # generate_statement after persistence === before persistence.
    after = svc.generate_statement(cust.id, ps, pe)
    for key in ("opening_balance", "closing_balance", "total_charges",
                "total_credits", "aging", "lines", "period_start",
                "period_end", "as_of"):
        assert before[key] == after[key], f"live statement '{key}' changed"
    assert before["closing_balance"] == 200.0  # 250 invoiced − 50 paid


# ---------------------------------------------------------------------------
# 7. Statement form page lists history with archive links
# ---------------------------------------------------------------------------

def test_statement_form_lists_history_with_archive_link(db, client):
    cust = _customer(db)
    _open_invoice(db, cust, amount=60.0, due_days_ago=2)
    start, end = _window()
    assert client.get(
        f"/customers/{cust.id}/statement/print?start={start}&end={end}"
    ).status_code == 200
    row = _rows_for(db, cust)[0]

    r = client.get(f"/customers/{cust.id}/statement")
    assert r.status_code == 200
    assert "Statement History" in r.text
    assert row.statement_number in r.text
    assert f"/customers/{cust.id}/statement/archive/{row.id}" in r.text
