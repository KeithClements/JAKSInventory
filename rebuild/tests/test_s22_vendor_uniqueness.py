"""
tests/test_s22_vendor_uniqueness.py
===================================
Lane B — Vendor name + code uniqueness (Phase 0, Risk #2).

Locks in three guarantees:

  1. Router-level probe (both create paths) rejects:
       * a second ACTIVE vendor with the same name (case-insensitive)
       * a second vendor with the same non-empty vendor_code
  2. DB-level partial unique indexes are the backstop behind the probe:
       * a second ACTIVE "PAI" violates uq_vendors_name_active
       * a second non-empty vendor_code violates uq_vendors_vendor_code
  3. Two vendors with an EMPTY vendor_code are allowed (partial index skips '').
     A deactivated vendor's name may be reused (partial index WHERE is_active=1).

Run:
    python -m pytest tests/test_s22_vendor_uniqueness.py -q
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
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.models.vendor import Vendor

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c  # entering the client triggers startup → seeds admin #1


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── 1. Router probe — full create ─────────────────────────────────────────────

def test_full_create_rejects_duplicate_active_name_case_insensitive(client, db):
    code1 = f"A{next(_seq)}"[:4]
    r1 = client.post("/vendors/new", data={"name": "PAI", "vendor_code": code1},
                     follow_redirects=False)
    assert r1.status_code == 303  # created → redirect to detail

    # Second active "pai" (different case, different code) is rejected.
    r2 = client.post("/vendors/new", data={"name": "pai", "vendor_code": f"B{next(_seq)}"[:4]},
                     follow_redirects=False)
    assert r2.status_code == 422
    assert "already exists" in r2.text.lower()

    # Only one active PAI in the DB.
    active_pai = (
        db.query(Vendor)
        .filter(Vendor.is_active == True)  # noqa: E712
        .filter(Vendor.name.ilike("pai"))
        .count()
    )
    assert active_pai == 1


def test_full_create_rejects_duplicate_vendor_code(client, db):
    code = f"C{next(_seq)}"[:4]
    r1 = client.post("/vendors/new", data={"name": f"VendorX{next(_seq)}", "vendor_code": code},
                     follow_redirects=False)
    assert r1.status_code == 303

    r2 = client.post("/vendors/new", data={"name": f"VendorY{next(_seq)}", "vendor_code": code},
                     follow_redirects=False)
    assert r2.status_code == 422
    assert "already used" in r2.text.lower() or "unique" in r2.text.lower()

    assert db.query(Vendor).filter(Vendor.vendor_code == code).count() == 1


# ── 2. Router probe — quick create ────────────────────────────────────────────

def test_quick_create_rejects_duplicate_name(client, db):
    code1 = f"D{next(_seq)}"[:4]
    r1 = client.post("/vendors/quick-create",
                     data={"name": "Interstate-McBee", "vendor_code": code1},
                     follow_redirects=False)
    assert r1.status_code == 200  # quick-create returns an HTMX toast on success

    r2 = client.post("/vendors/quick-create",
                     data={"name": "interstate-mcbee", "vendor_code": f"E{next(_seq)}"[:4]},
                     follow_redirects=False)
    assert r2.status_code == 422
    assert "already exists" in r2.text.lower()


def test_quick_create_rejects_duplicate_code(client, db):
    code = f"F{next(_seq)}"[:4]
    r1 = client.post("/vendors/quick-create",
                     data={"name": f"QC{next(_seq)}", "vendor_code": code},
                     follow_redirects=False)
    assert r1.status_code == 200

    r2 = client.post("/vendors/quick-create",
                     data={"name": f"QC{next(_seq)}", "vendor_code": code},
                     follow_redirects=False)
    assert r2.status_code == 422
    assert "already used" in r2.text.lower() or "unique" in r2.text.lower()


# ── 3. DB-level backstop indexes ──────────────────────────────────────────────

def test_db_index_blocks_second_active_name(db):
    """A direct ORM insert (bypassing the router probe) must still be blocked by
    the partial unique index uq_vendors_name_active."""
    db.add(Vendor(name="DBNameDup", vendor_code="", is_active=True))
    db.commit()
    db.add(Vendor(name="DBNameDup", vendor_code="", is_active=True))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_db_index_blocks_second_nonempty_code(db):
    db.add(Vendor(name=f"CodeDupA{next(_seq)}", vendor_code="ZZ9", is_active=True))
    db.commit()
    db.add(Vendor(name=f"CodeDupB{next(_seq)}", vendor_code="ZZ9", is_active=True))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_db_index_allows_empty_vendor_code_duplicates(db):
    """vendor_code defaults to '' for most vendors; the partial index must let
    blank codes repeat freely."""
    db.add(Vendor(name=f"BlankCodeA{next(_seq)}", vendor_code="", is_active=True))
    db.add(Vendor(name=f"BlankCodeB{next(_seq)}", vendor_code="", is_active=True))
    db.commit()  # must NOT raise
    blanks = db.query(Vendor).filter(Vendor.vendor_code == "").count()
    assert blanks >= 2


def test_db_index_allows_name_reuse_after_deactivation(db):
    """Partial index is WHERE is_active = 1, so a deactivated vendor's name can
    be taken by a new active vendor."""
    old = Vendor(name="ReusableName", vendor_code="", is_active=True)
    db.add(old)
    db.commit()
    old.is_active = False
    db.commit()
    db.add(Vendor(name="ReusableName", vendor_code="", is_active=True))
    db.commit()  # must NOT raise
    assert (
        db.query(Vendor)
        .filter(Vendor.name == "ReusableName", Vendor.is_active == True)  # noqa: E712
        .count()
        == 1
    )
