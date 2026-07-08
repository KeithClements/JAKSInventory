"""
tests/test_pdf_branding.py
==========================
Phase 2 §5.12 — PDF / document branding BACKEND SEAM.

Covers: the 3 seeded settings; get_company_dict()'s empty-safe branding fields
(logo_url None when unset → templates keep the text header, no regression); and
the admin-gated logo-upload route (stores under static/uploads, writes
company_logo_path, rejects non-images + oversize). No print templates touched.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
import app.routers.settings as settings_mod
from app.models.setting import Setting
from app.services.document_render import get_company_dict
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)

# Minimal valid 1x1 PNG.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _set(db, key, value):
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value, label=key))
    db.commit()


# ── 1. Seed ────────────────────────────────────────────────────────────────────

def test_branding_settings_seeded(db):
    # startup seed_settings ran via the client fixture
    keys = {s.key for s in db.query(Setting).all()}
    assert {"company_logo_path", "document_footer_text", "document_show_logo",
            "document_logo_height"} <= keys


# ── 2. get_company_dict empty-safe (no regression) ─────────────────────────────

def test_company_dict_empty_safe(db):
    _set(db, "company_logo_path", "")
    _set(db, "document_show_logo", "true")
    _set(db, "document_footer_text", "")
    c = get_company_dict(db)
    assert c["logo_url"] is None          # → templates fall back to the text header
    assert c["show_logo"] is True
    assert c["footer_text"] == ""
    # existing keys unchanged
    assert c["name"] and "phone" in c and "address" in c


def test_company_dict_resolves_logo_url(db):
    _set(db, "company_logo_path", "uploads/logo_abc.png")
    assert get_company_dict(db)["logo_url"] == "/static/uploads/logo_abc.png"


def test_company_dict_show_logo_false(db):
    _set(db, "document_show_logo", "false")
    assert get_company_dict(db)["show_logo"] is False
    _set(db, "document_show_logo", "true")   # reset for other tests


def test_company_dict_footer_text(db):
    _set(db, "document_footer_text", "Thank you for your business.")
    assert get_company_dict(db)["footer_text"] == "Thank you for your business."
    _set(db, "document_footer_text", "")


# ── 2b. Owner-adjustable logo height (empty-safe + clamped) ────────────────────

def test_company_dict_logo_height_default(db):
    _set(db, "document_logo_height", "56")
    assert get_company_dict(db)["logo_height"] == 56


def test_company_dict_logo_height_custom(db):
    _set(db, "document_logo_height", "96")
    assert get_company_dict(db)["logo_height"] == 96
    _set(db, "document_logo_height", "56")   # reset for other tests


def test_company_dict_logo_height_clamped(db):
    # absurd values are clamped to the safe 24–160 px range, never break the doc
    _set(db, "document_logo_height", "9999")
    assert get_company_dict(db)["logo_height"] == 160
    _set(db, "document_logo_height", "1")
    assert get_company_dict(db)["logo_height"] == 24
    _set(db, "document_logo_height", "56")   # reset


def test_company_dict_logo_height_non_numeric_falls_back(db):
    _set(db, "document_logo_height", "huge please")
    assert get_company_dict(db)["logo_height"] == 56     # invalid → default
    _set(db, "document_logo_height", "")
    assert get_company_dict(db)["logo_height"] == 56     # blank → default
    _set(db, "document_logo_height", "56")   # reset


# ── 3. Upload route ────────────────────────────────────────────────────────────

def test_upload_logo_valid(client, db, monkeypatch, tmp_path):
    # Keep the artifact out of the repo's static/uploads.
    monkeypatch.setattr(settings_mod, "_UPLOAD_DIR", tmp_path)
    r = client.post("/settings/logo",
                    files={"file": ("logo.png", _PNG, "image/png")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "logo_saved" in r.headers["location"]
    path = db.query(Setting).filter(Setting.key == "company_logo_path").first().value
    assert path.startswith("uploads/company_logo_") and path.endswith(".png")
    # file actually written, and get_company_dict resolves it
    assert (tmp_path / pathlib.Path(path).name).exists()
    assert get_company_dict(db)["logo_url"] == f"/static/{path}"


def test_upload_logo_rejects_non_image(client, db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_mod, "_UPLOAD_DIR", tmp_path)
    before = db.query(Setting).filter(Setting.key == "company_logo_path").first().value
    r = client.post("/settings/logo",
                    files={"file": ("evil.txt", b"not an image", "text/plain")},
                    follow_redirects=False)
    # Full-page form → redirect back to settings with a friendly error, not raw JSON.
    assert r.status_code == 303
    assert "logo_error" in r.headers["location"]
    db.expire_all()
    after = db.query(Setting).filter(Setting.key == "company_logo_path").first().value
    assert after == before                # setting unchanged


def test_upload_logo_rejects_oversize(client, db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_mod, "_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "_MAX_LOGO_BYTES", 8)   # tiny cap
    before = db.query(Setting).filter(Setting.key == "company_logo_path").first().value
    r = client.post("/settings/logo",
                    files={"file": ("big.png", _PNG, "image/png")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "logo_error" in r.headers["location"]
    db.expire_all()
    after = db.query(Setting).filter(Setting.key == "company_logo_path").first().value
    assert after == before                # oversize never written


def test_upload_logo_rejects_wrong_extension(client, db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_mod, "_UPLOAD_DIR", tmp_path)
    # image content-type but a disallowed extension → rejected
    r = client.post("/settings/logo",
                    files={"file": ("logo.svg", _PNG, "image/svg+xml")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "logo_error" in r.headers["location"]
