"""
tests/test_ai_description_routes.py
====================================
UI surfaces for the AI description generator:

  • Settings → AI tab (within the main /settings/ page) and the standalone
    /settings/ai page both render the key form.
  • POST /settings/ai persists the key (encrypted at rest, verified through the
    service helper — never against the raw column).
  • POST /products/ai-suggest returns the panel partial with the three suggested
    fields when the SDK is mocked.
  • The "Suggest with AI" button appears on /products/new and /products/{id}.

The Anthropic SDK is MOCKED — no network is hit.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.main import app
from app.models.product import Product, ProductCategory
from app.models.setting import Setting
from app.models.vendor import Vendor
from app.services import ai_description_service as ai_mod
from app.services.ai_description_service import (
    DEFAULT_MODEL,
    get_anthropic_api_key,
    set_anthropic_api_key,
)
from tests.conftest import activate, fresh_engine


_ENGINE = fresh_engine()
_TEST_KEY = "sk-ant-test-AAAAAAAAA"
_TEST_FERNET_KEY = "k1nzAQ7BR4_OEjpa6mTHmDfn0gMqaJOG8mE7Tnv8Eko="


@pytest.fixture(scope="module")
def _client():
    activate(_ENGINE)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean(_client, monkeypatch):
    monkeypatch.setenv("JAKS_FERNET_KEY", _TEST_FERNET_KEY)
    monkeypatch.setattr(ai_mod, "_warned_no_key", False, raising=False)

    SL = activate(_ENGINE)
    db = SL()
    db.query(Setting).filter(Setting.key.like("anthropic%")).delete()
    db.query(Setting).filter(Setting.key.like("ai_%")).delete()
    db.query(Product).delete()
    db.query(Vendor).delete()
    db.query(ProductCategory).delete()
    db.commit()
    db.close()
    yield


def _session():
    return activate(_ENGINE)()


# ── Anthropic SDK stub (same shape the panel test in test_ai_description_service
#    uses — kept minimal here so the two test files don't need a shared helper). ─
class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, **input_data):
        self.input = dict(input_data)
        self.name = "save_description"


class _FakeUsage:
    def __init__(self, input_tokens=128, output_tokens=256):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, blocks, usage=None):
        self.content = list(blocks)
        self.usage = usage or _FakeUsage()
        self.model = DEFAULT_MODEL


class _FakeMessages:
    def __init__(self, resp):
        self._resp = resp
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


def _patch_anthropic(monkeypatch, resp):
    holder = {}

    class _FakeAnthropic:
        def __init__(self, api_key=None):
            holder["api_key"] = api_key
            holder["messages"] = _FakeMessages(resp)
            self.messages = holder["messages"]

    monkeypatch.setitem(
        sys.modules, "anthropic",
        type("anthropic", (), {"Anthropic": _FakeAnthropic}),
    )
    return holder


# ── 1. GET /settings/ai (standalone) renders the form ───────────────────────
def test_get_settings_ai_standalone_renders_key_field(_client):
    r = _client.get("/settings/ai")
    assert r.status_code == 200
    body = r.text
    # The form has the API key input + the model field
    assert 'name="anthropic_api_key"' in body
    assert 'name="ai_description_model"' in body
    # Header text
    assert "Anthropic" in body or "Claude" in body


# ── 2. GET /settings/ (main tabs) renders the AI tab body ──────────────────
def test_get_settings_main_renders_ai_tab(_client):
    r = _client.get("/settings/")
    assert r.status_code == 200
    body = r.text
    # AI tab button
    assert "tab = 'ai'" in body
    # AI tab content — API key input + model dropdown options
    assert 'name="anthropic_api_key"' in body
    assert "claude-sonnet-4-6" in body
    assert "claude-haiku-4-5-20251001" in body
    assert "claude-opus-4-8" in body
    # Status chip — "Not configured" when no key
    assert "Not configured" in body


def test_get_settings_main_status_chip_shows_configured(_client):
    # Set a key — main tab should now show "Configured"
    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
    finally:
        db.close()
    r = _client.get("/settings/")
    assert r.status_code == 200
    body = r.text
    # The status chip says Configured
    assert "Configured" in body
    # And the key plaintext NEVER appears anywhere
    assert _TEST_KEY not in body


# ── 3. POST /settings/ai persists the key (verified via service helper) ────
def test_post_settings_ai_persists_key_via_service(_client):
    r = _client.post(
        "/settings/ai",
        data={"anthropic_api_key": _TEST_KEY, "ai_description_model": "claude-sonnet-4-6"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text
    # Verified via the SERVICE — not by reading the raw column.
    db = _session()
    try:
        assert get_anthropic_api_key(db) == _TEST_KEY
    finally:
        db.close()


def test_post_settings_ai_from_main_tab_redirects_to_tab(_client):
    # Referrer header tells the route the click came from the main tab,
    # so it should redirect back to /settings/?tab=ai (not /settings/ai).
    r = _client.post(
        "/settings/ai",
        data={"anthropic_api_key": _TEST_KEY, "ai_description_model": "claude-sonnet-4-6"},
        headers={"referer": "http://testserver/settings/"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text
    loc = r.headers.get("location", "")
    assert "/settings/" in loc
    assert "tab=ai" in loc
    assert "ai_saved=1" in loc


# ── 4. POST /products/ai-suggest returns the panel partial w/ 3 fields ─────
def test_post_products_ai_suggest_renders_panel_with_three_fields(_client, monkeypatch):
    msg = _FakeMessage(
        [_FakeToolUseBlock(
            title="Cummins ISX Water Pump",
            description="Direct-fit water pump for Cummins ISX engines. Cast iron housing.",
            meta_description="Cummins ISX water pump replacement — direct fit.",
        )],
        usage=_FakeUsage(input_tokens=432, output_tokens=187),
    )
    _patch_anthropic(monkeypatch, msg)

    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
    finally:
        db.close()

    r = _client.post(
        "/products/ai-suggest",
        data={
            "vendor_part_number": "4920477",
            "manufacturer": "Cummins",
            "engine_make": "Cummins",
            "engine_model": "ISX",
        },
    )
    assert r.status_code == 200, r.text
    body = r.text
    # All three suggested values appear in the panel
    assert "Cummins ISX Water Pump" in body
    assert "Direct-fit water pump" in body
    assert "Cummins ISX water pump replacement" in body
    # Per-field Accept buttons + Accept all
    assert 'data-ai-field="title"' in body
    assert 'data-ai-field="description"' in body
    assert 'data-ai-field="meta_description"' in body
    assert "Accept all" in body
    # Token counts surface in the header
    assert "432" in body and "187" in body
    # The API key NEVER appears anywhere
    assert _TEST_KEY not in body


def test_post_products_ai_suggest_no_key_renders_friendly_panel(_client):
    # No key configured — endpoint returns the panel with the error envelope.
    r = _client.post("/products/ai-suggest", data={"manufacturer": "Cummins"})
    assert r.status_code == 200, r.text
    body = r.text
    assert "Settings" in body
    # Friendly link to the AI tab
    assert "/settings/?tab=ai" in body or "/settings/ai" in body
    # NOT a Python traceback / 500
    assert "Traceback" not in body


# ── 5. The Suggest button appears on /products/new ─────────────────────────
def test_suggest_with_ai_button_on_products_new(_client):
    r = _client.get("/products/new")
    assert r.status_code == 200
    body = r.text
    # The button is HTMX-wired to /products/ai-suggest
    assert "Suggest with AI" in body
    assert 'hx-post="/products/ai-suggest"' in body
    assert 'id="ai-suggestion-panel"' in body
    # hx-sync prevents stacked requests
    assert 'hx-sync="this:replace"' in body
    # @ai-accept handler is wired on the form
    assert "ai-accept" in body
    # Meta description field is present so Accept can write to it
    assert 'name="meta_description"' in body


# ── 6. The Suggest button appears on the product detail page ────────────────
def test_suggest_with_ai_button_on_products_detail(_client):
    # Need a product to render the page.
    db = _session()
    try:
        p = Product(
            sku="JAKS-TEST-AI-001",
            title="Test Pump",
            description="A test pump",
            is_active=True,
        )
        db.add(p)
        db.commit()
        pid = p.id
    finally:
        db.close()

    r = _client.get(f"/products/{pid}")
    assert r.status_code == 200, r.text
    body = r.text
    assert "Suggest with AI" in body
    assert 'hx-post="/products/ai-suggest"' in body
    assert 'id="ai-suggestion-panel"' in body
    # @ai-accept handler is wired on the form
    assert "ai-accept" in body
    # Meta description field is present so Accept can write to it
    assert 'name="meta_description"' in body


# ── 7. The aiSuggestionPanel() factory ships GLOBALLY (before any HTMX swap) ──
#
# Regression guard for the Accept-does-nothing bug: the panel is delivered by an
# HTMX swap, and Alpine initializes its `x-data="aiSuggestionPanel()"` during the
# htmx settle phase — BEFORE any <script> inside the swapped fragment runs. If the
# factory lives only in the fragment it loses that race ("aiSuggestionPanel is not
# defined" → empty Alpine scope → the Accept buttons are dead). It must therefore
# be defined on `window` by base.html, which every full page renders up-front.
def test_ai_factory_defined_globally_on_full_page(_client):
    # A full page that extends base.html must carry the factory definition so it
    # exists before Alpine ever initializes a swapped-in panel.
    r = _client.get("/products/new")
    assert r.status_code == 200
    assert "window.aiSuggestionPanel" in r.text, (
        "aiSuggestionPanel() must be defined in base.html (before deferred Alpine), "
        "not inside the HTMX-swapped panel fragment — see _ai_suggestion_panel.html."
    )


def test_ai_panel_fragment_does_not_redefine_factory(_client, monkeypatch):
    # The swapped fragment must NOT carry its own copy of the factory: that's the
    # race we removed, and a second definition would re-introduce the foot-gun.
    msg = _FakeMessage(
        [_FakeToolUseBlock(
            title="X", description="Y", meta_description="Z",
        )],
    )
    _patch_anthropic(monkeypatch, msg)
    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
    finally:
        db.close()

    r = _client.post("/products/ai-suggest", data={"manufacturer": "Cummins"})
    assert r.status_code == 200, r.text
    # The panel still wires the Accept buttons via Alpine…
    assert 'data-ai-field="title"' in r.text
    # …but does NOT define the factory itself (lives in base.html now).
    assert "window.aiSuggestionPanel =" not in r.text
