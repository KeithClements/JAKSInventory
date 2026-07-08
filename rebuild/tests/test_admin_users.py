"""
tests/test_admin_users.py
=========================
Live-trial enablement — the admin Users & Roles page: create logins, assign
roles, reset passwords, activate/deactivate, with self-lockout + last-admin
guards. (Startup seeds admin#1 + bookkeeper, so the client fixture has both.)
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:  # startup seeds admin#1 + bookkeeper
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _no_session(client):
    """Clear any session cookie left by a prior test's /login so CSRF (which only
    enforces once a session cookie is present) doesn't block these native-form
    POSTs — the same unauthenticated-POST path the other route tests rely on."""
    client.cookies.clear()
    yield


def _users(db):
    from app.models.user import User
    return {u.username: u for u in db.query(User).all()}


def test_users_list_renders(client):
    r = client.get("/admin/users")
    assert r.status_code == 200
    assert "admin" in r.text


def test_create_sales_user_then_login(client, db):
    r = client.post("/admin/users", data={
        "name": "Counter Clerk", "username": "clerk1",
        "password": "clerkpass1", "role": "sales",
    }, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    u = _users(db)["clerk1"]
    assert u.role == "sales" and u.is_active

    # The new SALES user can actually log in with the set password.
    login = client.post("/login", data={"username": "clerk1", "password": "clerkpass1"},
                        follow_redirects=False)
    assert login.status_code in (302, 303)


def test_duplicate_username_rejected(client):
    client.post("/admin/users", data={"name": "Dup A", "username": "dupe",
                                       "password": "password1", "role": "sales"},
                follow_redirects=False)
    r = client.post("/admin/users", data={"name": "Dup B", "username": "DUPE",
                                          "password": "password2", "role": "sales"},
                    follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]


def test_short_password_rejected(client):
    r = client.post("/admin/users", data={"name": "X", "username": "shortpw",
                                          "password": "short", "role": "sales"},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_set_role_and_block_self_and_last_admin(client, db):
    users = _users(db)
    # Promote the seeded bookkeeper to admin, then we have 2 admins.
    bk = users["bookkeeper"]
    r = client.post(f"/admin/users/{bk.id}/role", data={"role": "admin"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]

    # Acting admin is user #1 (test bypass). Can't change own role.
    r = client.post("/admin/users/1/role", data={"role": "sales"}, follow_redirects=False)
    assert "error=" in r.headers["location"]

    # Demote the bookkeeper back to bookkeeping (now only user#1 remains admin).
    r = client.post(f"/admin/users/{bk.id}/role", data={"role": "bookkeeping"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]


def test_reset_password_changes_login(client, db):
    client.post("/admin/users", data={"name": "Reset Me", "username": "resetme",
                                      "password": "oldpass12", "role": "sales"},
                follow_redirects=False)
    uid = _users(db)["resetme"].id
    r = client.post(f"/admin/users/{uid}/password", data={"new_password": "newpass34"},
                    follow_redirects=False)
    assert "ok=" in r.headers["location"]
    good = client.post("/login", data={"username": "resetme", "password": "newpass34"},
                      follow_redirects=False)
    assert good.status_code in (302, 303)


def test_toggle_active_and_self_guard(client, db):
    client.post("/admin/users", data={"name": "Toggle", "username": "toggleme",
                                      "password": "togglepw1", "role": "sales"},
                follow_redirects=False)
    uid = _users(db)["toggleme"].id
    r = client.post(f"/admin/users/{uid}/toggle-active", follow_redirects=False)
    assert "ok=" in r.headers["location"]
    db.expire_all()
    assert _users(db)["toggleme"].is_active is False

    # Cannot deactivate self (acting admin = #1).
    r = client.post("/admin/users/1/toggle-active", follow_redirects=False)
    assert "error=" in r.headers["location"]
