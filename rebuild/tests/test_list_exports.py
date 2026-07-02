"""
tests/test_list_exports.py
==========================
Lane I — real CSV export on the Sales Orders and Payments list screens.

  * GET /sales-orders/export.csv and GET /payments/export.csv stream text/csv
    with an attachment filename, the expected header row, and every matching
    row (not a single visible page).
  * Each export honors the SAME tab/q filters as its list view
    (_apply_so_list_filters / _apply_payment_list_filters are shared with the
    list routes, so this pins the shared chain end-to-end).
  * Auth perimeter: with JAKS_SKIP_AUTH unset, an unauthenticated GET is
    303-redirected to /login by enforce_login (mirrors
    tests/test_auth_enforcement.py — the route itself carries no user dep,
    same as /invoices/export.csv and /customers/export.csv).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_list_exports.py -q
"""
from __future__ import annotations

import csv
import io
import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    InvoiceStatus,
    PaymentMethod,
    PaymentStatus,
    SOStatus,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment, PaymentAllocation
from app.models.quote import SalesOrder, SOLine
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    # follow_redirects=False: the auth-perimeter tests observe the 303 itself.
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Builders ──────────────────────────────────────────────────────────────────

def _customer(db, name: str) -> Customer:
    c = Customer(company_name=name, is_active=True)
    db.add(c)
    db.commit()
    return c


def _so(db, cust: Customer, status: str, *, po: str | None = None) -> SalesOrder:
    n = next(_seq)
    so = SalesOrder(
        so_number=f"SO-2026-{n:04d}",
        customer_id=cust.id,
        status=status,
        customer_po_number=po,
    )
    so.lines.append(SOLine(description=f"Part {n}", qty_ordered=2,
                           qty_fulfilled=1, unit_price=100.0))
    db.add(so)
    db.commit()
    return so


def _payment(db, cust: Customer, amount: float, *, status: str = PaymentStatus.APPLIED,
             method: str = PaymentMethod.CHECK, check_number: str | None = None) -> Payment:
    p = Payment(
        customer_id=cust.id,
        amount_received=amount,
        payment_method=method,
        status=status,
        check_number=check_number,
    )
    db.add(p)
    db.commit()
    return p


def _rows(resp) -> list[list[str]]:
    return list(csv.reader(io.StringIO(resp.text)))


@pytest.fixture(scope="module")
def seeded(client):
    """One shared dataset for the whole module (read-only endpoints)."""
    s = _appdb.SessionLocal()
    try:
        acme = _customer(s, "Acme Diesel Export Co")
        bravo = _customer(s, "Bravo Trucks Export LLC")

        so_open = _so(s, acme, SOStatus.OPEN, po="PO-ACME-77")
        so_cancelled = _so(s, bravo, SOStatus.CANCELLED)

        pmt_applied = _payment(s, acme, 150.0, check_number="1042")
        pmt_reversed = _payment(s, bravo, 75.0, status=PaymentStatus.REVERSED)

        # Allocation → the applied payment's row carries the invoice number.
        inv = Invoice(invoice_number="INV-EXP-1", customer_id=acme.id,
                      status=InvoiceStatus.OPEN)
        s.add(inv)
        s.commit()
        s.add(PaymentAllocation(payment_id=pmt_applied.id, invoice_id=inv.id,
                                amount_applied=150.0))
        s.commit()

        return {
            "so_open_number": so_open.so_number,
            "so_cancelled_number": so_cancelled.so_number,
            "pmt_applied_id": pmt_applied.id,
            "pmt_reversed_id": pmt_reversed.id,
        }
    finally:
        s.close()


# ── Sales Orders export ───────────────────────────────────────────────────────

def test_so_export_streams_csv_with_header_and_rows(client, seeded):
    r = client.get("/sales-orders/export.csv")
    assert r.status_code == 200, r.text[:200]
    assert "text/csv" in r.headers.get("content-type", "")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "sales_orders.csv" in r.headers.get("content-disposition", "")

    rows = _rows(r)
    assert rows[0] == [
        "so_number", "status", "customer", "customer_po_number", "esn",
        "payment_mode", "deposit_amount", "lines",
        "qty_ordered", "qty_fulfilled", "subtotal", "ordered",
    ]
    by_number = {row[0]: row for row in rows[1:]}
    assert seeded["so_open_number"] in by_number
    assert seeded["so_cancelled_number"] in by_number

    open_row = by_number[seeded["so_open_number"]]
    assert open_row[1] == SOStatus.OPEN
    assert open_row[2] == "Acme Diesel Export Co"
    assert open_row[3] == "PO-ACME-77"
    assert open_row[7] == "1"          # 1 line
    assert open_row[8] == "2"          # qty ordered
    assert open_row[9] == "1"          # qty fulfilled
    assert open_row[10] == "200.00"    # 2 × $100


def test_so_export_respects_tab_filter(client, seeded):
    r = client.get("/sales-orders/export.csv?tab=open")
    numbers = [row[0] for row in _rows(r)[1:]]
    assert seeded["so_open_number"] in numbers
    assert seeded["so_cancelled_number"] not in numbers


def test_so_export_respects_q_filter(client, seeded):
    r = client.get("/sales-orders/export.csv?q=Bravo Trucks Export")
    numbers = [row[0] for row in _rows(r)[1:]]
    assert numbers == [seeded["so_cancelled_number"]]


# ── Payments export ───────────────────────────────────────────────────────────

def test_payments_export_streams_csv_with_header_and_rows(client, seeded):
    r = client.get("/payments/export.csv")
    assert r.status_code == 200, r.text[:200]
    assert "text/csv" in r.headers.get("content-type", "")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "payments.csv" in r.headers.get("content-disposition", "")

    rows = _rows(r)
    assert rows[0] == [
        "payment_id", "payment_date", "customer", "method", "check_number",
        "status", "amount_received", "surcharge", "amount_applied",
        "amount_unapplied", "nsf_fee", "applied_invoices", "reversal_reason",
    ]
    by_id = {row[0]: row for row in rows[1:]}
    applied = by_id[str(seeded["pmt_applied_id"])]
    assert applied[2] == "Acme Diesel Export Co"
    assert applied[3] == PaymentMethod.CHECK
    assert applied[4] == "1042"
    assert applied[5] == PaymentStatus.APPLIED
    assert applied[6] == "150.00"
    assert applied[8] == "150.00"      # fully allocated
    assert applied[9] == "0.00"        # nothing unapplied
    assert "INV-EXP-1" in applied[11]  # allocation's invoice number

    reversed_row = by_id[str(seeded["pmt_reversed_id"])]
    assert reversed_row[5] == PaymentStatus.REVERSED
    assert reversed_row[6] == "75.00"


def test_payments_export_respects_tab_filter(client, seeded):
    r = client.get("/payments/export.csv?tab=reversed")
    ids = [row[0] for row in _rows(r)[1:]]
    assert str(seeded["pmt_reversed_id"]) in ids
    assert str(seeded["pmt_applied_id"]) not in ids


def test_payments_export_respects_q_filter(client, seeded):
    r = client.get("/payments/export.csv?q=1042")  # check-number search
    ids = [row[0] for row in _rows(r)[1:]]
    assert ids == [str(seeded["pmt_applied_id"])]


# ── Auth perimeter — unauthenticated GET redirects to /login ─────────────────

@pytest.mark.parametrize("path", [
    "/sales-orders/export.csv",
    "/payments/export.csv",
])
def test_export_unauthenticated_redirects_to_login(client, seeded, path, monkeypatch):
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)  # real enforcement ON
    r = client.get(path)
    assert r.status_code in (302, 303), (
        f"expected an auth redirect, got {r.status_code} (handler ran unauthenticated?)")
    assert "/login" in r.headers.get("location", "")


def test_export_control_reachable_with_skip_auth(client, seeded, monkeypatch):
    """Control: the SAME endpoints answer 200 under the normal suite env,
    proving the redirect above is the auth layer, not a broken route."""
    monkeypatch.setenv("JAKS_SKIP_AUTH", "1")
    for path in ("/sales-orders/export.csv", "/payments/export.csv"):
        assert client.get(path).status_code == 200
