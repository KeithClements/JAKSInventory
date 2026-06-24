"""
tests/test_ai_backfill_seo.py
=============================
AIDescriptionService.backfill_seo generates AND PERSISTS seo_description (+
seo_title when blank) for products lacking SEO — the write-back the manual
"Suggest with AI" button never does. The Anthropic call is mocked (no network).
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import app.database as _appdb
from app.models.product import Product
from app.services.ai_description_service import AIDescriptionService
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _mk(db, *, seo_description="", seo_title="", title="Reman Injector"):
    n = next(_seq)
    p = Product(sku=f"JAKS-PAI-SEO{n:04d}", title=title,
                seo_description=seo_description, seo_title=seo_title)
    db.add(p)
    db.commit()
    return p


def _ok_suggestion(**kw):
    return {"title": "Cummins N14 Injector", "description": "Reman injector for N14.",
            "meta_description": "Reman Cummins N14 fuel injector - direct fit, OEM cross-ref.",
            "model": "claude-sonnet-4-6", "tokens_in": 10, "tokens_out": 20}


def _svc(db, suggestion=_ok_suggestion):
    svc = AIDescriptionService(db, None)
    svc.is_configured = lambda: True               # type: ignore[assignment]
    svc.suggest_for_product = suggestion           # type: ignore[assignment]
    return svc


def test_fills_blank_seo_description_and_title(db):
    p = _mk(db)
    res = _svc(db).backfill_seo()
    assert res["ok"] and res["generated"] == 1 and res["failed"] == 0
    db.refresh(p)
    assert p.seo_description.startswith("Reman Cummins N14")
    assert p.seo_title == "Cummins N14 Injector"
    assert res["tokens_in"] == 10 and res["tokens_out"] == 20


def test_skips_products_that_already_have_seo(db):
    p = _mk(db, seo_description="Hand-written meta the owner wrote.")
    called = {"n": 0}

    def _suggest(**kw):
        called["n"] += 1
        return _ok_suggestion()

    svc = _svc(db, _suggest)
    res = svc.backfill_seo()
    # filtered out by the query (blank-only), so the API is never even called
    assert res["considered"] == 0 and called["n"] == 0
    db.refresh(p)
    assert p.seo_description == "Hand-written meta the owner wrote."


def test_overwrite_replaces_existing_seo(db):
    p = _mk(db, seo_description="old meta")
    res = _svc(db).backfill_seo(overwrite=True)
    assert res["generated"] == 1
    db.refresh(p)
    assert p.seo_description.startswith("Reman Cummins N14")


def test_does_not_clobber_a_curated_seo_title(db):
    p = _mk(db, seo_title="My Hand-Tuned Title")
    _svc(db).backfill_seo()
    db.refresh(p)
    assert p.seo_title == "My Hand-Tuned Title"      # title left alone
    assert p.seo_description.startswith("Reman Cummins N14")  # desc still filled


def test_dry_run_writes_nothing(db):
    p = _mk(db)
    called = {"n": 0}

    def _suggest(**kw):
        called["n"] += 1
        return _ok_suggestion()

    res = _svc(db, _suggest).backfill_seo(dry_run=True)
    assert res["generated"] == 1 and res["dry_run"] is True
    assert called["n"] == 0                          # no API call on a dry run
    db.refresh(p)
    assert p.seo_description == ""                    # nothing written


def test_scoped_to_product_ids(db):
    a = _mk(db)
    b = _mk(db)
    res = _svc(db).backfill_seo([a.id])
    assert res["considered"] == 1
    db.refresh(a); db.refresh(b)
    assert a.seo_description != "" and b.seo_description == ""


def test_limit_caps_the_run(db):
    for _ in range(3):
        _mk(db)
    res = _svc(db).backfill_seo(limit=2)
    assert res["considered"] == 2 and res["generated"] == 2


def test_no_api_key_is_failsoft(db):
    _mk(db)
    res = AIDescriptionService(db, None).backfill_seo()   # not configured
    assert res["ok"] is False and res["error"] == "no_api_key"


def test_per_product_api_error_is_counted_not_fatal(db):
    _mk(db); _mk(db)
    svc = _svc(db, lambda **kw: {"error": "api_error", "message": "boom"})
    res = svc.backfill_seo()
    assert res["failed"] == 2 and res["generated"] == 0 and res["ok"] is True


def test_missing_key_midrun_stops_early(db):
    _mk(db); _mk(db)
    svc = _svc(db, lambda **kw: {"error": "no_api_key", "message": "gone"})
    res = svc.backfill_seo()
    assert res["ok"] is False and res["error"] == "no_api_key"
    assert res["generated"] == 0
