"""
tests/test_engine_catalog.py
============================
Standardized engine make/model catalog + the cascading picker macro that replaces
free-text engine entry on the quote/job header (owner request 2026-06-06). Locks the
data contract so the ERP can later filter parts / suggest products / cross-reference
on a CLEAN make+model instead of "Cat"/"CAT"/"Caterpillar" or "C15 Acert"/"c-15".
"""
from __future__ import annotations

from app.constants import ENGINE_MAKES, ENGINE_MODELS_BY_MAKE
from app.routers.quotes import templates


# ── Catalog invariants ────────────────────────────────────────────────────────

def test_makes_include_expected_presets_and_other_last():
    expected = [
        "CAT / Caterpillar", "Cummins", "Detroit", "Paccar", "Mack",
        "Volvo", "International / Navistar", "Mercedes", "Other",
    ]
    for mk in expected:
        assert mk in ENGINE_MAKES, f"{mk} missing from ENGINE_MAKES"
    assert ENGINE_MAKES[-1] == "Other"


def test_every_make_has_models_ending_in_other():
    # Every selectable make needs a non-empty model list ending in "Other" so a
    # custom value is always reachable (Mercedes/Other have only "Other").
    for mk in ENGINE_MAKES:
        assert mk in ENGINE_MODELS_BY_MAKE, f"{mk} missing from ENGINE_MODELS_BY_MAKE"
        models = ENGINE_MODELS_BY_MAKE[mk]
        assert models, f"{mk} has an empty model list"
        assert models[-1] == "Other", f"{mk} model list must end with 'Other'"


def test_no_orphan_model_keys():
    for mk in ENGINE_MODELS_BY_MAKE:
        assert mk in ENGINE_MAKES, f"{mk} keyed in models but not offered in ENGINE_MAKES"


def test_known_models_present():
    assert "C15" in ENGINE_MODELS_BY_MAKE["CAT / Caterpillar"]
    assert "ISX" in ENGINE_MODELS_BY_MAKE["Cummins"]
    assert "DD15" in ENGINE_MODELS_BY_MAKE["Detroit"]
    assert "MaxxForce 13" in ENGINE_MODELS_BY_MAKE["International / Navistar"]
    assert ENGINE_MODELS_BY_MAKE["Mercedes"] == ["Other"]  # listed without presets


# ── Picker macro rendering ────────────────────────────────────────────────────

def _render(make="", model=""):
    tpl = templates.env.from_string(
        '{% from "macros/engine_picker.html" import engine_picker %}'
        "{{ engine_picker(mk, md, makes, models) }}"
    )
    return tpl.render(mk=make, md=model, makes=ENGINE_MAKES, models=ENGINE_MODELS_BY_MAKE)


def test_macro_renders_dropdowns_and_hidden_fields():
    html = _render(make="Cummins", model="ISX")
    # Submits through the existing columns → autosave / carry-forward unchanged.
    assert 'name="engine_manufacturer"' in html
    assert 'name="engine_model"' in html
    assert 'type="hidden"' in html
    # Both labelled dropdowns are present.
    assert "Engine Make" in html and "Engine Model" in html
    # Seed values handed to Alpine via data-* attributes (house pattern; no
    # tojson-inside-an-attribute, which would break the markup).
    assert 'data-make="Cummins"' in html
    assert 'data-model="ISX"' in html
    # The make→model map is embedded as JSON in <script> text content (the safe place).
    assert "data-engine-json" in html
    assert '"Cummins"' in html  # part of the embedded JSON map


def test_macro_defaults_are_empty_when_unset():
    html = _render()  # brand-new quote, no engine info yet
    assert 'data-make=""' in html
    assert 'data-model=""' in html
    # Make picker still lists every preset for selection.
    assert "CAT / Caterpillar" in html
    assert "International / Navistar" in html


def test_macro_field_names_are_overridable():
    # Reusability: the same widget can target other columns later (e.g. on products).
    tpl = templates.env.from_string(
        '{% from "macros/engine_picker.html" import engine_picker %}'
        "{{ engine_picker('', '', makes, models, 'engine_make', 'engine_model') }}"
    )
    html = tpl.render(makes=ENGINE_MAKES, models=ENGINE_MODELS_BY_MAKE)
    assert 'name="engine_make"' in html
    assert 'name="engine_model"' in html
