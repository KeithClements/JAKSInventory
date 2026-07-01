"""
tests/test_customer_validation.py
=================================
QA fixes (headed-browser report) for the Customer create/update paths:

  1. Duplicate-customer detection was EMAIL-ONLY. A second customer with a
     DIFFERENT email but the SAME phone saved silently → two records share a
     phone, the exact fragmentation the email dedup prevents (counter staff look
     people up by phone). Now phone is deduped on NORMALIZED digits, mirroring
     the email-dedup UX (warn + "View Existing"), with a "Create Anyway"
     override since phone is not a unique key. Blank phone is EXEMPT.

  2. Invalid email was accepted server-side ("notanemail" saved). Now create &
     update reject a malformed email with a 4xx / clear error; blank stays OK.

Covers, per the verification list:
  (a) a 2nd customer with the same normalized phone (diff email) is warned/blocked
  (b) differently-formatted same phone — "(720) 555-1234" vs "7205551234" — collides
  (c) a blank phone does NOT collide
  (d) an invalid email is rejected server-side
  (e) a valid email and a blank email are accepted

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_customer_validation.py -q
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.main import app
from app.models.customer import Customer
from app.services.customer_service import (
    CustomerService, is_valid_email, normalize_phone,
)
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


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


def _cust(db, *, name, phone="", email="", active=True):
    c = Customer(company_name=name, contact_name="QA", phone=phone, email=email,
                 is_active=active)
    db.add(c); db.commit(); db.refresh(c)
    return c


# ════════════════════════════════════════════════════════════════════════════
# Unit: the normalization rule + the email validator (the shared contract)
# ════════════════════════════════════════════════════════════════════════════

def test_normalize_phone_collapses_formats_and_country_code():
    # Punctuation, spacing and a leading country-code "1" all normalize away.
    assert normalize_phone("(720) 555-1234") == "7205551234"
    assert normalize_phone("720.555.1234") == "7205551234"
    assert normalize_phone("720-555-1234") == "7205551234"
    assert normalize_phone("+1 (720) 555-1234") == "7205551234"
    assert normalize_phone("1-720-555-1234") == "7205551234"
    # Blank / None → empty key (never collide phoneless customers).
    assert normalize_phone("") == ""
    assert normalize_phone(None) == ""
    assert normalize_phone("   ") == ""


def test_is_valid_email_accepts_real_rejects_junk():
    assert is_valid_email("name@example.com")
    assert is_valid_email("a.b+tag@sub.example.co")
    assert not is_valid_email("notanemail")
    assert not is_valid_email("a@b")          # no TLD dot
    assert not is_valid_email("a b@c.com")    # space in local part
    assert not is_valid_email("@example.com") # empty local part
    assert not is_valid_email("")             # blank is not "valid" — callers gate on blank


# ════════════════════════════════════════════════════════════════════════════
# Unit: find_by_phone (the dedup key the router calls)
# ════════════════════════════════════════════════════════════════════════════

def test_find_by_phone_matches_differently_formatted_same_number(db):
    n = next(_seq)
    ac = 500 + n  # per-run unique area code (avoid cross-test phone collisions)
    existing = _cust(db, name=f"Phone{n} Co", phone=f"({ac}) 555-1234")
    # (b) a differently-formatted SAME phone is detected as the same customer.
    hit = CustomerService(db).find_by_phone(f"{ac}5551234")
    assert hit is not None and hit.id == existing.id
    # leading country code variant too
    hit2 = CustomerService(db).find_by_phone(f"+1 {ac} 555 1234")
    assert hit2 is not None and hit2.id == existing.id


def test_find_by_phone_blank_never_collides(db):
    n = next(_seq)
    # Two phoneless customers must NOT collapse together.
    _cust(db, name=f"NoPhoneA{n}", phone="")
    _cust(db, name=f"NoPhoneB{n}", phone="")
    # (c) blank phone → no match.
    assert CustomerService(db).find_by_phone("") is None
    assert CustomerService(db).find_by_phone("   ") is None


def test_find_by_phone_excludes_self_and_inactive(db):
    n = next(_seq)
    ac = 600 + n  # per-run unique area code
    me = _cust(db, name=f"Self{n}", phone=f"{ac}-555-9000")
    _cust(db, name=f"Archived{n}", phone=f"{ac}-555-9000", active=False)
    # Editing myself must not flag myself; inactive rows are never warned on.
    assert CustomerService(db).find_by_phone(f"{ac}5559000", exclude_id=me.id) is None


# ════════════════════════════════════════════════════════════════════════════
# Route: POST /customers/new — phone dedup parity + email validation
# ════════════════════════════════════════════════════════════════════════════

def test_create_warns_on_duplicate_phone_then_creates_on_confirm(client, db):
    n = next(_seq)
    # Use a per-run unique area code so a same-normalized phone from another test
    # in this module-shared engine can't be the one find_by_phone surfaces first.
    fmt = f"({400 + n}) 555-1234"
    digits = f"{400 + n}5551234"
    existing = _cust(db, name=f"Acme{n} Diesel", phone=fmt,
                     email=f"first{n}@example.com")
    before = db.query(Customer).count()

    # (a) 2nd customer, DIFFERENT email, SAME phone (differently formatted) →
    # warns (re-renders form), does NOT create.
    r = client.post("/customers/new", data={
        "company_name": f"Totally Different {n}",
        "email": f"second{n}@example.com",
        "phone": digits,
    })
    assert r.status_code == 200
    db.expire_all()
    assert db.query(Customer).count() == before, "duplicate-phone customer created without confirm"
    assert existing.company_name in r.text, "phone warning did not surface the existing match"
    assert "/customers/%d" % existing.id in r.text, "no View Existing link to the phone match"

    # "Create Anyway" (confirm_duplicate=1) overrides → creates it.
    r2 = client.post("/customers/new", data={
        "company_name": f"Totally Different {n}",
        "email": f"second{n}@example.com",
        "phone": digits,
        "confirm_duplicate": "1",
    }, follow_redirects=False)
    assert r2.status_code == 303
    db.expire_all()
    assert db.query(Customer).count() == before + 1, "confirmed create did not persist"


def test_create_rejects_invalid_email(client, db):
    n = next(_seq)
    before = db.query(Customer).count()
    # (d) invalid email is rejected server-side (4xx), customer NOT created.
    r = client.post("/customers/new", data={
        "company_name": f"BadEmail{n}",
        "email": "notanemail",
    })
    assert r.status_code == 422, f"expected 422 for invalid email, got {r.status_code}"
    db.expire_all()
    assert db.query(Customer).count() == before, "customer with invalid email was created"


def test_create_accepts_valid_and_blank_email(client, db):
    n = next(_seq)
    before = db.query(Customer).count()
    # (e) valid email accepted.
    r1 = client.post("/customers/new", data={
        "company_name": f"GoodEmail{n}",
        "email": f"good{n}@example.com",
    }, follow_redirects=False)
    assert r1.status_code == 303
    # blank email accepted (no phone → no dedup either).
    r2 = client.post("/customers/new", data={
        "company_name": f"BlankEmail{n}",
        "email": "",
    }, follow_redirects=False)
    assert r2.status_code == 303
    db.expire_all()
    assert db.query(Customer).count() == before + 2


# ════════════════════════════════════════════════════════════════════════════
# Route: POST /customers/{id} — update path validation parity
# ════════════════════════════════════════════════════════════════════════════

def test_update_rejects_invalid_email(client, db):
    n = next(_seq)
    c = _cust(db, name=f"EditMe{n}", email=f"orig{n}@example.com")
    r = client.post(f"/customers/{c.id}", data={
        "company_name": c.company_name,
        "email": "stillnotanemail",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers.get("location", "")
    db.expire_all()
    # Email was NOT mutated to the bad value.
    assert db.get(Customer, c.id).email == f"orig{n}@example.com"


def test_update_blocks_phone_taken_by_another_customer(client, db):
    n = next(_seq)
    ac = 700 + n  # per-run unique area code
    owner = _cust(db, name=f"PhoneOwner{n}", phone=f"{ac}-555-7777")
    editing = _cust(db, name=f"Editing{n}", phone="")
    # Try to set editing's phone to one owner already uses (different format).
    r = client.post(f"/customers/{editing.id}", data={
        "company_name": editing.company_name,
        "phone": f"({ac}) 555-7777",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers.get("location", "")
    db.expire_all()
    # The conflicting phone was NOT written.
    assert normalize_phone(db.get(Customer, editing.id).phone) != f"{ac}5557777"

    # Saving the SAME customer with its OWN phone unchanged must NOT self-block.
    r2 = client.post(f"/customers/{owner.id}", data={
        "company_name": owner.company_name,
        "phone": f"{ac}-555-7777",
    }, follow_redirects=False)
    assert r2.status_code == 303
    assert "error=" not in r2.headers.get("location", "")
