"""
tests/test_customer_import_gate.py
==================================
POST /customers/import/confirm — RBAC gate + per-row guard-rail parity.

The confirm route used to have NO user dependency at all: any request that
reached the handler bulk-wrote the customer master, bypassing the email
validation and dedup rails the single-create path enforces, and one bad row
rolled back the whole batch. This module pins the fix:

  1. RBAC — the route resolves the acting user and gates on
     Permission.IMPORT_CUSTOMERS (SALES denied, ADMIN allowed). RBAC runs
     against REAL seeded User rows: BaseService.assert_can always role-enforces
     a user id that maps to an actual row, even under JAKS_SKIP_AUTH.
  2. Auth perimeter — with JAKS_SKIP_AUTH unset, an unauthenticated POST is
     303-redirected to /login by enforce_login (mirrors
     tests/test_auth_enforcement.py).
  3. Per-row parity — invalid-email, duplicate-email and out-of-range-discount
     rows are SKIPPED with a reason (batch continues); the skip_duplicates
     checkbox controls the name dedup, which is EXACT-normalized-name only:
     the single-create substring warn is override-able there but silently
     dropped DISTINCT companies here ('Cummins Northwest' skipped because
     'Cummins' exists) — the import no longer substring-skips.
  4. Per-row commit — a poisoned row (constraint violation at INSERT) skips
     that row only; every other valid row still persists.
  5. Standing-discount clamp at the SOURCE (single create/edit): the document
     services now reject out-of-range discounts, so a bad stored customer
     default would brick '+ Quote' / 'New Invoice' for that customer — the
     create form 422-re-renders and the edit bounces with a flash instead of
     storing it.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_customer_import_gate.py -q
"""
from __future__ import annotations

import json
import pathlib
import sys
from urllib.parse import unquote

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE, hash_password, make_session_token
from app.constants import UserRole
from app.main import app
from app.models.customer import Customer
from app.models.user import User
from tests.conftest import activate, fresh_engine

_CONFIRM = "/customers/import/confirm"


# ── Harness ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def env(monkeypatch):
    """Fresh in-memory engine per test (counts are deterministic) with the
    normal suite env (JAKS_SKIP_AUTH=1 → login middleware + CSRF bypassed;
    real seeded User rows are still role-enforced by assert_can)."""
    monkeypatch.setenv("JAKS_SKIP_AUTH", "1")
    engine = fresh_engine()
    return activate(engine)


@pytest.fixture()
def client(env):
    # No lifespan context: startup seeding is skipped so this module fully
    # controls which User rows exist (and therefore which uid maps to which role).
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


def _seed_user(SessionLocal, role: str, username: str) -> int:
    db = SessionLocal()
    try:
        u = User(
            name=username,
            username=username,
            password_hash=hash_password(f"{username}-pw-not-default"),
            role=role,
            is_active=True,
        )
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _login_as(client: TestClient, user_id: int) -> None:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, make_session_token(user_id))


def _post_confirm(client: TestClient, rows: list, skip_duplicates: str = "1"):
    return client.post(
        _CONFIRM,
        data={"import_json": json.dumps(rows), "skip_duplicates": skip_duplicates},
    )


def _company_names(SessionLocal) -> set[str]:
    db = SessionLocal()
    try:
        return {c.company_name for c in db.query(Customer).all()}
    finally:
        db.close()


def _seed_customer(SessionLocal, name: str, email: str = "") -> None:
    db = SessionLocal()
    try:
        db.add(Customer(company_name=name, email=email))
        db.commit()
    finally:
        db.close()


# ── 0. Route sanity — denial tests must not false-pass on a 404 ───────────────

def test_import_confirm_is_a_registered_route():
    registered = {
        (m, getattr(r, "path", None))
        for r in app.routes
        for m in (getattr(r, "methods", None) or set())
    }
    assert ("POST", _CONFIRM) in registered, (
        "POST /customers/import/confirm is no longer registered — the RBAC "
        "tests below would false-pass on a 404")


# ── 1. Auth perimeter — unauthenticated blocked when enforcement is ON ────────

def test_unauthenticated_blocked_when_auth_enforced(env, client, monkeypatch):
    monkeypatch.delenv("JAKS_SKIP_AUTH", raising=False)  # real enforcement ON
    r = _post_confirm(client, [{"company_name": "Perimeter Test Co"}])
    assert r.status_code in (302, 303), (
        f"expected an auth redirect, got {r.status_code} (handler ran unauthenticated?)")
    assert "/login" in r.headers.get("location", "")
    assert "Perimeter Test Co" not in _company_names(env)


# ── 2. RBAC — SALES denied, ADMIN allowed ─────────────────────────────────────

def test_sales_user_denied(env, client):
    sales_id = _seed_user(env, UserRole.SALES, "clerk")
    _login_as(client, sales_id)

    r = _post_confirm(client, [{"company_name": "Clerk Import Co"}])
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert loc.startswith("/customers/import?error="), (
        f"SALES was not bounced to the import screen with an error: {loc!r}")
    assert "permission" in loc.lower()
    assert "Clerk Import Co" not in _company_names(env), (
        "SALES user's import WROTE customers despite the permission denial")


def test_admin_succeeds(env, client):
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    r = _post_confirm(client, [
        {"company_name": "Admin Import One", "email": "one@import.test"},
        {"company_name": "Admin Import Two"},
    ])
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert loc.startswith("/customers/?ok="), f"unexpected redirect: {loc!r}"
    assert "Imported 2" in loc
    names = _company_names(env)
    assert {"Admin Import One", "Admin Import Two"} <= names


# ── 3. Per-row parity — email validation ──────────────────────────────────────

def test_invalid_email_row_skipped_others_import(env, client):
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    r = _post_confirm(client, [
        {"company_name": "Good Row A", "email": "a@ok.test"},
        {"company_name": "Bad Email Row", "email": "notanemail"},
        {"company_name": "Good Row B"},
    ])
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert "Imported 2" in loc
    assert "skipped 1" in loc
    assert "1 invalid email" in loc, f"skip reason missing from summary: {loc!r}"
    names = _company_names(env)
    assert {"Good Row A", "Good Row B"} <= names
    assert "Bad Email Row" not in names


# ── 4. Per-row parity — HARD email dedup (independent of skip_duplicates) ─────

def test_duplicate_email_row_skipped_even_with_skip_duplicates_off(env, client):
    _seed_customer(env, "Existing Email Owner", email="dup@x.test")
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    r = _post_confirm(client, [
        {"company_name": "Totally Different Name", "email": "dup@x.test"},
        {"company_name": "Clean Row", "email": "clean@x.test"},
    ], skip_duplicates="0")
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert "Imported 1" in loc
    assert "1 duplicate email" in loc, f"skip reason missing: {loc!r}"
    names = _company_names(env)
    assert "Clean Row" in names
    assert "Totally Different Name" not in names, (
        "a row sharing an existing customer's email was imported — the hard "
        "email dedup (uq_customers_email parity) is not enforced per row")


def test_in_batch_duplicate_email_second_row_skipped(env, client):
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    r = _post_confirm(client, [
        {"company_name": "First Email Claim", "email": "same@batch.test"},
        {"company_name": "Second Email Claim", "email": "same@batch.test"},
    ], skip_duplicates="0")
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert "Imported 1" in loc and "1 duplicate email" in loc
    names = _company_names(env)
    assert "First Email Claim" in names
    assert "Second Email Claim" not in names


# ── 5. skip_duplicates semantics — EXACT-normalized name only ─────────────────

def test_duplicate_name_respects_skip_duplicates_checkbox(env, client):
    _seed_customer(env, "Mikes Diesel Repair")
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    batch = [
        {"company_name": "mikes diesel repair"},      # exact (case-insensitive)
        {"company_name": "Mike's-Diesel Repair"},     # exact after normalization
        {"company_name": "Unrelated Machine Shop"},
    ]

    r = _post_confirm(client, batch, skip_duplicates="1")
    loc = unquote(r.headers.get("location", ""))
    assert "Imported 1" in loc
    assert "2 duplicate" in loc, f"expected both name-dupes skipped: {loc!r}"
    names = _company_names(env)
    assert "Unrelated Machine Shop" in names
    assert "mikes diesel repair" not in names
    assert "Mike's-Diesel Repair" not in names

    # Unchecking skip_duplicates is the import's "Create Anyway" — the same
    # name rows import now (email dedup would still hold, but these have none).
    r2 = _post_confirm(client, [
        {"company_name": "mikes diesel repair"},
        {"company_name": "Mike's-Diesel Repair"},
    ], skip_duplicates="0")
    loc2 = unquote(r2.headers.get("location", ""))
    assert "Imported 2" in loc2, f"skip_duplicates=0 must allow name dupes: {loc2!r}"
    names = _company_names(env)
    assert {"mikes diesel repair", "Mike's-Diesel Repair"} <= names


def test_substring_name_no_longer_skips_distinct_company(env, client):
    """The old dedup reused the single-create FUZZY rule (normalized substring
    either direction), which silently dropped DISTINCT companies from an import
    with no per-row override: 'Cummins Northwest' was skipped because 'Cummins'
    exists. Import dedup is now EXACT-normalized-name only — the substring row
    imports, while a true normalized-exact dupe still skips."""
    _seed_customer(env, "Cummins")
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    r = _post_confirm(client, [
        {"company_name": "Cummins Northwest"},   # distinct company — must import
        {"company_name": "CUMMINS!"},            # normalized-exact dupe — skips
    ], skip_duplicates="1")
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert "Imported 1" in loc, (
        f"'Cummins Northwest' was substring-skipped against 'Cummins': {loc!r}")
    assert "1 duplicate" in loc
    names = _company_names(env)
    assert "Cummins Northwest" in names, (
        "a distinct company sharing a name prefix was dropped by the import")
    assert "CUMMINS!" not in names


# ── 5b. Per-row parity — standing-discount clamp ──────────────────────────────

def test_out_of_range_discount_row_skipped_others_import(env, client):
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    r = _post_confirm(client, [
        {"company_name": "Good Discount Co", "discount_pct": 10},
        {"company_name": "Poisoned Discount Co", "discount_pct": 150},
        {"company_name": "No Discount Co"},
    ])
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert "Imported 2" in loc
    assert "1 invalid discount" in loc, f"skip reason missing from summary: {loc!r}"
    names = _company_names(env)
    assert {"Good Discount Co", "No Discount Co"} <= names
    assert "Poisoned Discount Co" not in names, (
        "an out-of-range standing discount was imported — that row would brick "
        "quote/invoice creation for the customer (document services reject it)")
    db = env()
    try:
        good = db.query(Customer).filter(
            Customer.company_name == "Good Discount Co").first()
        assert good.discount_pct == 10.0
    finally:
        db.close()


# ── 6. Per-row commit — a poisoned row cannot roll back the batch ─────────────

def test_poisoned_row_does_not_roll_back_valid_rows(env, client):
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    # import_json is client-controlled, so a nested object can reach a Text
    # column; sqlite3 refuses to bind a dict at INSERT — a failure the
    # pre-checks cannot predict, so it exercises the commit-time skip path.
    r = _post_confirm(client, [
        {"company_name": "Survivor Before"},
        {"company_name": "Poison Row", "notes": {"bad": "type"}},
        {"company_name": "Survivor After"},
    ])
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert "Imported 2" in loc, (
        f"valid rows were lost to the poisoned row (whole-batch rollback?): {loc!r}")
    assert "1 database error" in loc, f"poisoned-row skip reason missing: {loc!r}"
    names = _company_names(env)
    assert {"Survivor Before", "Survivor After"} <= names
    assert "Poison Row" not in names


# ── 7. Standing-discount clamp at the source (single create / edit) ───────────

def test_create_form_rejects_out_of_range_discount(env, client):
    """POST /customers/new with discount_pct=150 → 422 re-render of the form
    (same shape as the email-format rejection), and NO customer row: a stored
    out-of-range default would brick '+ Quote' / 'New Invoice' for that
    customer now that the document services reject bad values."""
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    r = client.post("/customers/new", data={
        "company_name": "Discount Gate Co",
        "discount_pct": "150",
    })
    assert r.status_code == 422, (
        f"expected the 422 form re-render, got {r.status_code} "
        "(303 = the bad discount was SAVED)")
    assert "between 0 and 100" in r.text
    assert "Discount Gate Co" not in _company_names(env), (
        "a customer with a 150% standing discount was created")


def test_edit_rejects_out_of_range_discount_stored_unchanged(env, client):
    admin_id = _seed_user(env, UserRole.ADMIN, "boss")
    _login_as(client, admin_id)

    db = env()
    try:
        c = Customer(company_name="Edit Discount Co", discount_pct=5.0)
        db.add(c)
        db.commit()
        cust_id = c.id
    finally:
        db.close()

    r = client.post(f"/customers/{cust_id}", data={
        "company_name": "Edit Discount Co",
        "discount_pct": "-10",
    })
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert f"/customers/{cust_id}" in loc and "error=" in loc, loc
    assert "between 0 and 100" in loc, (
        f"edit with discount -10 was not refused with the flash: {loc!r}")

    db = env()
    try:
        fresh = db.query(Customer).filter(Customer.id == cust_id).first()
        assert fresh.discount_pct == 5.0, (
            "the rejected edit still overwrote the stored standing discount")
        assert fresh.company_name == "Edit Discount Co"
    finally:
        db.close()
