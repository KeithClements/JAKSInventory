"""
tests/test_ai_description_service.py
====================================
AIDescriptionService — Claude-powered product copy generator (title /
description / meta_description) used by the "Suggest with AI" button on
the product form. The Anthropic SDK is MOCKED — no network is ever hit.

Coverage:
  * Configuration helpers (is_configured, set/get_anthropic_api_key)
  * On-disk encryption (raw DB value ≠ plaintext; round-trip recovers it)
  * Service happy path: mocked client returns a tool_use block → service
    surfaces title/description/meta_description + model + usage tokens
  * Service fail-soft: no API key → {"error": "no_api_key", ...}
  * Service fail-soft: SDK raises → {"error": "api_error", ...}
  * Endpoint POST /products/ai-suggest:
      - no key configured → renders the friendly "Settings → AI" hint HTML
        (200, not 500)
      - happy path (mocked SDK) → renders panel with all three fields
  * Settings POST /settings/ai encrypts the key on disk (raw read ≠ plaintext)
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.main import app
from app.models.setting import Setting
from app.models.vendor import Vendor
from app.models.product import ProductCategory
from app.services import ai_description_service as ai_mod
from app.services.ai_description_service import (
    AIDescriptionService,
    DEFAULT_MODEL,
    get_anthropic_api_key,
    set_anthropic_api_key,
)
from tests.conftest import activate, fresh_engine


_ENGINE = fresh_engine()
_TEST_KEY = "sk-ant-test-1234567890"
# A known Fernet key used ONLY by this test module. Set per-test via monkeypatch
# (autouse fixture) so we don't pollute other modules' os.environ — the QBO
# tests assert raw plaintext tokens and would fail if JAKS_FERNET_KEY leaked.
_TEST_FERNET_KEY = "k1nzAQ7BR4_OEjpa6mTHmDfn0gMqaJOG8mE7Tnv8Eko="


@pytest.fixture(scope="module")
def _client():
    """Module-scoped TestClient. The context-manager form runs startup events
    which seed the default admin user (id=1) — needed for any route that goes
    through ``require_admin`` (e.g. POST /settings/ai)."""
    activate(_ENGINE)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean(_client, monkeypatch):
    # Per-test Fernet key so encryption round-trips work HERE without leaking
    # into other test modules (e.g. QBO tests assert plaintext token storage).
    # _warned_no_key is module-level state — reset so the "no key" warning
    # could re-arm if a later test clears the env (none currently do).
    monkeypatch.setenv("JAKS_FERNET_KEY", _TEST_FERNET_KEY)
    monkeypatch.setattr(ai_mod, "_warned_no_key", False, raising=False)

    SL = activate(_ENGINE)
    db = SL()
    # NB: don't truncate User — the admin row seeded by startup is reused
    # across tests in this module. (Cheap to leave; expensive to re-seed.)
    db.query(Setting).filter(Setting.key.like("anthropic%")).delete()
    db.query(Setting).filter(Setting.key.like("ai_%")).delete()
    db.query(Vendor).delete()
    db.query(ProductCategory).delete()
    db.commit()
    db.close()
    yield


def _session():
    return activate(_ENGINE)()


# ── Fake Anthropic SDK objects ───────────────────────────────────────────────
class _FakeToolUseBlock:
    """Mimics the SDK's content-block API (attribute access, .type/.input)."""

    type = "tool_use"

    def __init__(self, **input_data):
        self.input = dict(input_data)
        self.name = "save_description"
        self.id = "toolu_test"


class _FakeUsage:
    def __init__(self, input_tokens=120, output_tokens=240):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, blocks, usage=None):
        self.content = list(blocks)
        self.usage = usage or _FakeUsage()
        self.stop_reason = "tool_use"
        self.model = DEFAULT_MODEL


class _FakeMessages:
    def __init__(self, response_or_exc):
        self._response_or_exc = response_or_exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._response_or_exc, Exception):
            raise self._response_or_exc
        return self._response_or_exc


class _FakeClient:
    def __init__(self, response_or_exc):
        self.messages = _FakeMessages(response_or_exc)
        # so the test can read .messages.calls back


def _patch_anthropic(monkeypatch, response_or_exc):
    """Patch ai_mod's import of anthropic with a minimal stub. Returns the
    FakeClient that will be constructed so tests can inspect call args."""
    holder = {}

    class _FakeAnthropic:
        def __init__(self, api_key=None):
            holder["api_key"] = api_key
            holder["client"] = _FakeClient(response_or_exc)
            # Caller will read .messages from this instance, so we delegate:
            self.messages = holder["client"].messages

    fake_mod = type("anthropic", (), {"Anthropic": _FakeAnthropic})
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)
    return holder


# ── 1. Configuration helpers ─────────────────────────────────────────────────
def test_is_configured_reflects_key_presence():
    db = _session()
    try:
        svc = AIDescriptionService(db)
        assert svc.is_configured() is False
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
        assert svc.is_configured() is True
    finally:
        db.close()


def test_set_anthropic_api_key_stores_encrypted():
    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
        raw_row = (
            db.query(Setting)
            .filter(Setting.key == "anthropic_api_key_encrypted")
            .first()
        )
        assert raw_row is not None
        # The raw column value MUST NOT contain the plaintext key.
        assert _TEST_KEY not in raw_row.value
        # And it must carry the Fernet "enc:" marker so reads decrypt properly.
        assert raw_row.value.startswith("enc:")
    finally:
        db.close()


def test_get_anthropic_api_key_round_trips_plaintext():
    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
        assert get_anthropic_api_key(db) == _TEST_KEY
    finally:
        db.close()


def test_set_anthropic_api_key_blank_clears():
    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
        assert get_anthropic_api_key(db) == _TEST_KEY
        set_anthropic_api_key(db, "")
        db.commit()
        assert get_anthropic_api_key(db) is None
    finally:
        db.close()


# ── 2. Service: happy path with mocked SDK ───────────────────────────────────
def test_suggest_for_product_returns_fields_and_usage(monkeypatch):
    msg = _FakeMessage(
        [
            _FakeToolUseBlock(
                title="Water Pump for Cummins ISX",
                description="Direct-fit water pump for Cummins ISX engines. "
                            "Replaces OEM 4920477. Cast iron housing.",
                meta_description="Cummins ISX water pump, OEM 4920477 replacement.",
            )
        ],
        usage=_FakeUsage(input_tokens=140, output_tokens=210),
    )
    holder = _patch_anthropic(monkeypatch, msg)

    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()

        out = AIDescriptionService(db).suggest_for_product(
            vendor_name="PAI Industries",
            vendor_part_number="4920477",
            manufacturer="Cummins",
            engine_make="Cummins",
            engine_model="ISX",
            category_path="Engine > Cooling > Water Pump",
            current_title=None,
            current_description=None,
        )
    finally:
        db.close()

    # Field contract
    assert out["title"] == "Water Pump for Cummins ISX"
    assert out["description"].startswith("Direct-fit water pump")
    assert out["meta_description"].startswith("Cummins ISX water pump")
    # Metadata
    assert out["model"] == DEFAULT_MODEL
    assert out["tokens_in"] == 140
    assert out["tokens_out"] == 210
    # API key was passed to the SDK constructor (and NEVER appears in `out`)
    assert holder["api_key"] == _TEST_KEY
    for v in out.values():
        assert _TEST_KEY not in str(v)


def test_suggest_for_product_forces_save_description_tool(monkeypatch):
    """The model MUST be asked to call our save_description tool."""
    msg = _FakeMessage(
        [_FakeToolUseBlock(title="t", description="d", meta_description="m")]
    )
    holder = _patch_anthropic(monkeypatch, msg)

    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
        AIDescriptionService(db).suggest_for_product(manufacturer="Cummins")
    finally:
        db.close()

    call = holder["client"].messages.calls[0]
    assert call["model"] == DEFAULT_MODEL
    assert call["tool_choice"] == {"type": "tool", "name": "save_description"}
    # The required schema fields are all present
    tool_schema = call["tools"][0]
    assert tool_schema["name"] == "save_description"
    props = tool_schema["input_schema"]["properties"]
    assert set(props) == {"title", "description", "meta_description"}
    assert set(tool_schema["input_schema"]["required"]) == {
        "title", "description", "meta_description",
    }
    # max_tokens is set (so a hanging response can't burn the budget)
    assert call["max_tokens"] == 1024


# ── 3. Service: fail-soft envelopes ──────────────────────────────────────────
def test_suggest_for_product_with_no_key_returns_friendly_error(monkeypatch):
    # Even with the SDK present, no key → no call, friendly envelope.
    monkeypatch.setitem(sys.modules, "anthropic",
                         type("anthropic", (), {"Anthropic": lambda **kw: None}))
    db = _session()
    try:
        out = AIDescriptionService(db).suggest_for_product(manufacturer="Cummins")
    finally:
        db.close()
    assert out["error"] == "no_api_key"
    assert "Settings" in out["message"]
    # MUST NOT raise, MUST NOT contain copy fields
    assert "title" not in out


def test_suggest_for_product_wraps_api_errors_as_envelope(monkeypatch):
    # Simulate an SDK exception (any class — service catches broadly).
    class _FakeAPIError(Exception):
        pass

    _patch_anthropic(monkeypatch, _FakeAPIError("boom: 500"))

    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
        out = AIDescriptionService(db).suggest_for_product(manufacturer="Cummins")
    finally:
        db.close()

    assert out["error"] == "api_error"
    assert "boom" in out["message"] or "AI request failed" in out["message"]
    assert "title" not in out


def test_suggest_for_product_wraps_auth_errors_with_friendly_hint(monkeypatch):
    class _AuthenticationError(Exception):
        pass

    _patch_anthropic(monkeypatch, _AuthenticationError("invalid key"))

    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
        out = AIDescriptionService(db).suggest_for_product(manufacturer="Cummins")
    finally:
        db.close()
    assert out["error"] == "api_error"
    assert "Settings" in out["message"] or "key" in out["message"].lower()


def test_suggest_for_product_handles_missing_tool_use_block(monkeypatch):
    # Response with NO tool_use block (only text) → bad_response envelope.
    class _TextBlock:
        type = "text"
        text = "sorry I can't"

    msg = _FakeMessage([_TextBlock()])
    _patch_anthropic(monkeypatch, msg)

    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
        out = AIDescriptionService(db).suggest_for_product(manufacturer="Cummins")
    finally:
        db.close()
    assert out["error"] == "bad_response"


# ── 4. Endpoint: POST /products/ai-suggest ───────────────────────────────────
def test_post_products_ai_suggest_no_key_returns_friendly_html(_client, monkeypatch):
    # No key, no SDK patch needed (service short-circuits).
    r = _client.post("/products/ai-suggest", data={"manufacturer": "Cummins"})
    assert r.status_code == 200
    body = r.text
    # Friendly hint should mention Settings, NOT a Python traceback.
    assert "Settings" in body
    assert "Traceback" not in body


def test_post_products_ai_suggest_happy_path_renders_panel(_client, monkeypatch):
    msg = _FakeMessage([
        _FakeToolUseBlock(
            title="Water Pump for Cummins ISX",
            description="Direct-fit water pump for Cummins ISX. Cast iron.",
            meta_description="Cummins ISX water pump, direct replacement.",
        )
    ])
    _patch_anthropic(monkeypatch, msg)

    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        # Seed a vendor + category so the endpoint can resolve names.
        v = Vendor(name="PAI Industries", vendor_code="PAI")
        db.add(v)
        cat = ProductCategory(name="Water Pump", level=1, is_active=True)
        db.add(cat)
        db.commit()
        vendor_id = v.id
        cat_id = cat.id
    finally:
        db.close()

    r = _client.post(
        "/products/ai-suggest",
        data={
            "vendor_id": str(vendor_id),
            "vendor_part_number": "4920477",
            "manufacturer": "Cummins",
            "engine_make": "Cummins",
            "engine_model": "ISX",
            "category_id": str(cat_id),
        },
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert "Water Pump for Cummins ISX" in body
    assert "Cast iron" in body
    assert "Cummins ISX water pump" in body
    # The API key MUST NOT appear in the rendered HTML
    assert _TEST_KEY not in body


# ── 5. Settings POST encrypts on disk ────────────────────────────────────────
def test_settings_ai_post_encrypts_key_on_disk(_client):
    r = _client.post(
        "/settings/ai",
        data={"anthropic_api_key": _TEST_KEY, "ai_description_model": ""},
        follow_redirects=False,
    )
    # 303 redirect on success
    assert r.status_code in (303, 302), r.text

    db = _session()
    try:
        raw_row = (
            db.query(Setting)
            .filter(Setting.key == "anthropic_api_key_encrypted")
            .first()
        )
        assert raw_row is not None
        # Raw on-disk value is encrypted (no plaintext leak).
        assert _TEST_KEY not in raw_row.value
        assert raw_row.value.startswith("enc:")
        # The helper round-trips it.
        assert get_anthropic_api_key(db) == _TEST_KEY
    finally:
        db.close()


def test_settings_ai_post_with_clear_wipes_key(_client):
    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
        assert get_anthropic_api_key(db) == _TEST_KEY
    finally:
        db.close()

    r = _client.post(
        "/settings/ai",
        data={"clear": "1"},
        follow_redirects=False,
    )
    assert r.status_code in (303, 302), r.text

    db = _session()
    try:
        assert get_anthropic_api_key(db) is None
    finally:
        db.close()


def test_settings_ai_get_renders_without_leaking_key(_client):
    db = _session()
    try:
        set_anthropic_api_key(db, _TEST_KEY)
        db.commit()
    finally:
        db.close()
    r = _client.get("/settings/ai")
    assert r.status_code == 200
    body = r.text
    # The plaintext key MUST NOT appear in the HTML at all.
    assert _TEST_KEY not in body
    # And the page should signal that AI is configured.
    assert "Configured" in body or "configured" in body
