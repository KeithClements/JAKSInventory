"""
tests/test_s21_pagination.py
============================
§21.10 — list pagination (replaces the silent limit(200) on the invoice list)
and the reusable compute_pager() helper.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.constants import InvoiceStatus
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.utils import compute_pager

_ENGINE = fresh_engine()
_seq = itertools.count(1)


def test_compute_pager_math():
    p = compute_pager(1, total=120, per_page=50)
    assert p["total_pages"] == 3 and p["start"] == 1 and p["end"] == 50 and p["offset"] == 0
    p2 = compute_pager(3, total=120, per_page=50)
    assert p2["start"] == 101 and p2["end"] == 120 and p2["offset"] == 100
    # out-of-range page clamps into range
    assert compute_pager(99, total=120, per_page=50)["page"] == 3
    assert compute_pager(0, total=120, per_page=50)["page"] == 1
    # empty set
    e = compute_pager(1, total=0, per_page=50)
    assert e["total_pages"] == 1 and e["start"] == 0 and e["end"] == 0


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


def test_invoice_list_paginates(client, db):
    c = Customer(company_name=f"Pg Co {next(_seq)}")
    db.add(c); db.flush()
    # 60 invoices → 2 pages at per_page=50.
    for _ in range(60):
        db.add(Invoice(invoice_number=f"INV-PG-{next(_seq):05d}", customer_id=c.id,
                       status=InvoiceStatus.OPEN))
    db.commit()

    r1 = client.get("/invoices/?tab=all&page=1")
    assert r1.status_code == 200
    assert "Next" in r1.text                 # pager rendered
    # Page 2 loads without error and shows Prev.
    r2 = client.get("/invoices/?tab=all&page=2")
    assert r2.status_code == 200
    assert "Prev" in r2.text
    # An absurd page number clamps, doesn't 500.
    assert client.get("/invoices/?tab=all&page=999").status_code == 200
