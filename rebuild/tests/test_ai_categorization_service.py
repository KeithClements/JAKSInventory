"""
tests/test_ai_categorization_service.py
=======================================
AICategorizationService — the optional Claude assist for the Smart Import review
queue. The Anthropic Messages API is mocked (no network); we assert the request we
build (model / headers / structured-output enum) and how the suggestion is written
back onto a candidate (resolved category + 🤖 AI flag, NEVER a status change).
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.main import app
from tests.conftest import activate, fresh_engine
from app.constants import ImportBatchStatus, ImportDisposition, ScrapedItemReviewStatus
from app.models.import_review import ImportBatch, ImportCandidate
from app.models.product import ProductCategory
from app.models.setting import Setting
from app.services import ai_categorization_service as ai_mod
from app.services.ai_categorization_service import AICategorizationService, AI_FLAG

_ENGINE = fresh_engine()
_RS = ScrapedItemReviewStatus


@pytest.fixture(autouse=True)
def _clean():
    SL = activate(_ENGINE)
    db = SL()
    db.query(ImportCandidate).delete()
    db.query(ImportBatch).delete()
    db.query(ProductCategory).delete()
    db.query(Setting).filter(Setting.key == "anthropic_api_key").delete()
    db.commit()
    db.close()
    yield


def _session():
    return activate(_ENGINE)()


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status
        self.text = text

    def json(self):
        return self._payload


def _ai_tool_response(obj: dict) -> dict:
    """Shape of an Anthropic forced-tool response: a tool_use block whose `input`
    is the schema-shaped object (§21 — replaced the old output_config/text JSON)."""
    return {
        "content": [{"type": "tool_use", "name": "classify_part",
                     "id": "toolu_test", "input": obj}],
        "stop_reason": "tool_use",
    }


def _seed():
    """Two-level catalog + a flagged candidate; returns ids."""
    db = _session()
    eng = ProductCategory(name="Engine", level=1, is_active=True)
    db.add(eng)
    db.flush()
    wp = ProductCategory(name="Water Pump", parent_id=eng.id, level=2, is_active=True)
    db.add(wp)
    db.flush()
    batch = ImportBatch(label="t", total=1, status=ImportBatchStatus.STAGED)
    db.add(batch)
    db.flush()
    cand = ImportCandidate(
        batch_id=batch.id, sku="PAI-123", title="Water Pump for Cummins ISX",
        category_name="Cooling",
        raw_json=json.dumps({"title": "Water Pump for Cummins ISX", "type": "Cooling",
                             "apps": ["Cummins|ISX"], "oem": ["Cummins|4920477"]}),
        disposition=ImportDisposition.NEW, needs_review=True,
        review_status=_RS.PENDING, resolved_category_id=None,
    )
    db.add(cand)
    db.commit()
    ids = {"eng": eng.id, "wp": wp.id, "batch": batch.id, "cand": cand.id}
    db.close()
    return ids


def _set_key(db, value="sk-ant-test"):
    db.add(Setting(key="anthropic_api_key", value=value, label=""))
    db.commit()


# ── gating ────────────────────────────────────────────────────────────────────
def test_disabled_without_key_makes_no_call(monkeypatch):
    ids = _seed()
    calls = []
    monkeypatch.setattr(ai_mod.httpx, "post", lambda *a, **k: calls.append(k) or _FakeResp({}))
    db = _session()
    svc = AICategorizationService(db)
    assert svc.is_enabled() is False
    summary = svc.bulk_categorize_flagged(ids["batch"])
    db.close()
    assert summary["processed"] == 0
    assert calls == []                      # never hit the API without a key
    assert summary["errors"]                # explains why


# ── request shape ─────────────────────────────────────────────────────────────
def test_request_uses_haiku_headers_and_enum(monkeypatch):
    ids = _seed()
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResp(_ai_tool_response(
            {"category_id": str(ids["wp"]), "confidence": "high",
             "engine_manufacturer": "Cummins", "reason": "water pump"}))

    monkeypatch.setattr(ai_mod.httpx, "post", fake_post)
    db = _session()
    _set_key(db)
    AICategorizationService(db).bulk_categorize_flagged(ids["batch"])
    db.close()

    assert captured["url"] == ai_mod.ANTHROPIC_URL
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == ai_mod.ANTHROPIC_VERSION
    body = captured["json"]
    assert body["model"] == "claude-haiku-4-5"
    # §21 — schema enforced via a forced tool call (no anthropic-beta header).
    assert body["tool_choice"] == {"type": "tool", "name": "classify_part"}
    tool = body["tools"][0]
    assert tool["name"] == "classify_part"
    schema = tool["input_schema"]
    enum = schema["properties"]["category_id"]["enum"]
    assert str(ids["wp"]) in enum and "UNKNOWN" in enum   # closed set of real ids
    assert schema["additionalProperties"] is False


# ── happy path: suggestion written, status untouched ──────────────────────────
def test_suggestion_sets_category_flag_and_notes_without_approving(monkeypatch):
    ids = _seed()
    monkeypatch.setattr(ai_mod.httpx, "post", lambda *a, **k: _FakeResp(_ai_tool_response(
        {"category_id": str(ids["wp"]), "confidence": "high",
         "engine_manufacturer": "Cummins", "reason": "clearly a water pump"})))
    db = _session()
    _set_key(db)
    summary = AICategorizationService(db).bulk_categorize_flagged(ids["batch"])
    assert summary == {"processed": 1, "suggested": 1, "no_match": 0, "errors": []}

    cand = db.get(ImportCandidate, ids["cand"])
    assert cand.resolved_category_id == ids["wp"]
    assert cand.category_issue is False
    assert AI_FLAG in cand.flags
    assert "Water Pump" in cand.flags          # breadcrumb label
    assert "clearly a water pump" in cand.review_notes
    assert cand.engine_manufacturer == "Cummins"
    # NEVER auto-approved — a human still confirms the status.
    assert cand.review_status == _RS.PENDING
    # needs_review is cleared when AI successfully assigns a category so that
    # "Approve all confident" can pick up AI-categorized rows without a manual
    # per-row action.
    assert cand.needs_review is False
    db.close()


# ── UNKNOWN → recorded as no-match, leaves category unset ──────────────────────
def test_unknown_leaves_category_unset(monkeypatch):
    ids = _seed()
    monkeypatch.setattr(ai_mod.httpx, "post", lambda *a, **k: _FakeResp(_ai_tool_response(
        {"category_id": "UNKNOWN", "confidence": "low",
         "engine_manufacturer": "", "reason": "ambiguous"})))
    db = _session()
    _set_key(db)
    summary = AICategorizationService(db).bulk_categorize_flagged(ids["batch"])
    assert summary["no_match"] == 1 and summary["suggested"] == 0

    cand = db.get(ImportCandidate, ids["cand"])
    assert cand.resolved_category_id is None
    assert f"{AI_FLAG}: no match" in cand.flags
    db.close()


# ── a fabricated (out-of-set) id is rejected, not trusted ─────────────────────
def test_out_of_set_id_is_rejected(monkeypatch):
    ids = _seed()
    monkeypatch.setattr(ai_mod.httpx, "post", lambda *a, **k: _FakeResp(_ai_tool_response(
        {"category_id": "999999", "confidence": "high",
         "engine_manufacturer": "", "reason": "made up"})))
    db = _session()
    _set_key(db)
    summary = AICategorizationService(db).bulk_categorize_flagged(ids["batch"])
    cand = db.get(ImportCandidate, ids["cand"])
    assert cand.resolved_category_id is None
    assert summary["no_match"] == 1
    db.close()


# ── auth error stops the run early ────────────────────────────────────────────
def test_auth_error_stops_run(monkeypatch):
    ids = _seed()
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return _FakeResp({}, status=401, text="invalid x-api-key")

    monkeypatch.setattr(ai_mod.httpx, "post", fake_post)
    db = _session()
    _set_key(db)
    summary = AICategorizationService(db).bulk_categorize_flagged(ids["batch"])
    assert summary["processed"] == 0
    assert summary["errors"]
    assert calls["n"] == 1                  # broke out instead of retrying every row
    db.close()


# ── route wiring ──────────────────────────────────────────────────────────────
def test_queue_shows_ai_button_when_key_set():
    ids = _seed()
    db = _session()
    _set_key(db)
    db.close()
    client = TestClient(app)
    r = client.get(f"/import-review/{ids['batch']}?tab=needs_review")
    assert r.status_code == 200
    assert "AI-categorize flagged" in r.text


def test_ai_categorize_without_key_redirects_to_flash():
    ids = _seed()
    client = TestClient(app)
    r = client.post(f"/import-review/{ids['batch']}/ai-categorize",
                    follow_redirects=False)
    assert r.status_code == 303
    assert "ai_error=nokey" in r.headers["location"]


# ── count helper ──────────────────────────────────────────────────────────────
def test_flagged_pending_count(monkeypatch):
    ids = _seed()
    db = _session()
    assert AICategorizationService(db).flagged_pending_count(ids["batch"]) == 1
    # a resolved one no longer counts toward the unresolved button total
    db.get(ImportCandidate, ids["cand"]).resolved_category_id = ids["wp"]
    db.commit()
    assert AICategorizationService(db).flagged_pending_count(ids["batch"]) == 0
    db.close()
