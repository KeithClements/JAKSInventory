"""
tests/test_public_links.py
==========================
§22.7 — signed public document links. Token security (sign/verify/expire/tamper),
URL gating on public_base_url, and the no-login /p/d/{token} route (served PDF for a
valid token; friendly 404 for a bad/expired token or an unrenderable doc).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
import app.routers.public_docs as public_docs
from app.main import app
from app.services.public_links import (
    make_doc_token, read_doc_token, public_base_url, public_doc_url,
)
from app.settings_utils import set_setting_value_db
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()


@pytest.fixture()
def db():
    SessionLocal = activate(_ENGINE)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(db):
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


# ── Token security ───────────────────────────────────────────────────────────

def test_token_roundtrip():
    tok = make_doc_token("quote", 42)
    assert read_doc_token(tok) == ("quote", 42)


def test_token_tamper_rejected():
    tok = make_doc_token("invoice", 7)
    assert read_doc_token(tok + "x") is None
    assert read_doc_token("garbage.not.a.token") is None
    assert read_doc_token("") is None


def test_token_expiry_rejected():
    tok = make_doc_token("sales_order", 3)
    # max_age=-1 → any age is "too old" → expired → None
    assert read_doc_token(tok, max_age=-1) is None


def test_unknown_doc_type_rejected():
    with pytest.raises(ValueError):
        make_doc_token("customer", 1)


def test_signed_nondict_payload_is_rejected():
    # A validly-signed but non-dict payload must return None, never raise.
    from app.services.public_links import _serializer
    bad = _serializer().dumps("not-a-dict")
    assert read_doc_token(bad) is None


# ── DRAFT status gate (never expose an internal draft) ───────────────────────

def test_draft_is_not_shareable():
    from app.routers.public_docs import _is_shareable

    class _D:
        def __init__(self, status):
            self.status = status

    assert _is_shareable(_D("draft")) is False
    assert _is_shareable(_D("DRAFT")) is False
    assert _is_shareable(_D("sent")) is True
    assert _is_shareable(_D("void")) is True     # already watermarked in the template
    assert _is_shareable(_D("paid")) is True


# ── URL gating on public_base_url ────────────────────────────────────────────

def test_no_url_when_base_unset(db):
    assert public_base_url(db) == ""
    assert public_doc_url(db, "quote", 1) is None


def test_url_built_when_base_set(db):
    set_setting_value_db(db, "public_base_url", "https://axle.jaksdiesel.com/")
    db.commit()
    url = public_doc_url(db, "quote", 99)
    assert url is not None
    assert url.startswith("https://axle.jaksdiesel.com/p/d/")  # trailing slash trimmed
    # the token in the URL round-trips back to the same doc
    token = url.rsplit("/", 1)[-1]
    assert read_doc_token(token) == ("quote", 99)


# ── Public route (no login) ──────────────────────────────────────────────────

def test_route_serves_pdf_for_valid_token(client, monkeypatch):
    monkeypatch.setattr(public_docs, "_render_doc_pdf",
                        lambda db, request, dt, di: b"%PDF-1.4 fake")
    tok = make_doc_token("quote", 1)
    r = client.get(f"/p/d/{tok}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "quote-1.pdf" in r.headers.get("content-disposition", "")
    assert r.content == b"%PDF-1.4 fake"


def test_route_404_for_bad_token(client):
    r = client.get("/p/d/totally-bogus-token")
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "expired or is invalid" in r.text


def test_route_404_when_doc_unrenderable(client, monkeypatch):
    # Valid token but the doc can't render (missing / no WeasyPrint) → friendly 404.
    monkeypatch.setattr(public_docs, "_render_doc_pdf",
                        lambda db, request, dt, di: None)
    tok = make_doc_token("invoice", 12345)
    r = client.get(f"/p/d/{tok}")
    assert r.status_code == 404
    assert "expired or is invalid" in r.text


def test_route_never_500s_on_render_error(client, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("render blew up")
    monkeypatch.setattr(public_docs, "_render_doc_pdf", _boom)
    tok = make_doc_token("sales_order", 5)
    r = client.get(f"/p/d/{tok}")
    assert r.status_code == 404  # swallowed, never leaked to an anonymous visitor
