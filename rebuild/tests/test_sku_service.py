"""
tests/test_sku_service.py
=========================
JAKS customer-facing SKU scheme (owner-locked 2026-06-06):

    JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]

Guards: engine-code normalization, category-code derivation, string assembly,
per-(engine,category) sequence allocation, and the vendor-twin rule (a 2nd
vendor's version of the SAME part shares the sequence with a different vendor
digit → readable equivalence 90001 ↔ 30001).
"""
from __future__ import annotations

import pytest

from app.models.product import Product, ProductCategory, ProductVendorSource
from app.models.vendor import Vendor
from app.services.sku_service import (
    SkuService, assemble_sku, derive_category_code, engine_code,
)
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db_session():
    engine = fresh_engine()
    SessionLocal = activate(engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── pure: engine-code normalization ─────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("ISX SERIES", "ISX"),
    ("DT466 ENGINE", "DT466"),
    ("DT466E HEUI ENGINES", "DT466"),     # canonical 'DT466' found inside the messy string
    ("MX-13 SERIES", "MX13"),
    ("D11 ENGINE", "D11"),
    ("DD15", "DD15"),
    ("E6 2  VALVE ENGINES", "E6"),
    ("DMM MODEL", "DMM"),                 # no canonical match → first meaningful token
    ("530 ENGINE", "530"),
    ("", ""),                              # multi-fit / engine-agnostic → omitted
    ("   ", ""),
])
def test_engine_code(raw, expected):
    assert engine_code(raw) == expected


# ── pure: category-code derivation (fallback when no explicit code) ─────────────
@pytest.mark.parametrize("name,expected", [
    ("ENGINE PARTS", "ENG"),
    ("TRANSMISSION", "TRAN"),
    ("DIFFERENTIAL", "DIFF"),
    ("CAB", "CAB"),
    ("AIR BRAKE PARTS", "AIRB"),
    ("", "GEN"),
])
def test_derive_category_code(name, expected):
    assert derive_category_code(name) == expected


# ── pure: string assembly ───────────────────────────────────────────────────────
def test_assemble_sku_with_engine():
    assert assemble_sku("INJ", "9", 1, engine_code="ISX") == "JAKS-ISX-INJ-90001"


def test_assemble_sku_engine_omitted():
    assert assemble_sku("GSK", "9", 1) == "JAKS-GSK-90001"


def test_assemble_sku_pads_sequence_to_four():
    assert assemble_sku("ENG", "9", 42) == "JAKS-ENG-90042"
    assert assemble_sku("ENG", "9", 1234) == "JAKS-ENG-91234"


# ── DB: sequence allocation + minting ───────────────────────────────────────────
def _cat(db, name, code=""):
    c = ProductCategory(name=name, code=code, level=1, is_active=True)
    db.add(c)
    db.flush()
    return c


def _vendor(db, name, code, number):
    v = Vendor(name=name, vendor_code=code, vendor_number=number, is_active=True)
    db.add(v)
    db.flush()
    return v


def _product(db, cat, engine_model=""):
    p = Product(sku=f"TMP-{id(object())}", title="x", category=cat,
                engine_model=engine_model, is_active=True)
    db.add(p)
    db.flush()
    return p


def test_assign_new_sku_increments_per_engine_category(db_session):
    db = db_session
    svc = SkuService(db)
    inj = _cat(db, "Injectors", code="INJ")
    pai = _vendor(db, "PAI Industries", "PAI", "9")

    p1 = _product(db, inj, engine_model="ISX SERIES")
    p2 = _product(db, inj, engine_model="ISX SERIES")
    assert svc.assign_new_sku(p1, pai) == "JAKS-ISX-INJ-90001"
    assert svc.assign_new_sku(p2, pai) == "JAKS-ISX-INJ-90002"
    # frozen components are stamped
    assert (p1.engine_code, p1.category_code, p1.part_seq) == ("ISX", "INJ", 1)


def test_assign_new_sku_omits_engine_when_multifit(db_session):
    db = db_session
    svc = SkuService(db)
    inj = _cat(db, "Injectors", code="INJ")
    pai = _vendor(db, "PAI Industries", "PAI", "9")
    p = _product(db, inj, engine_model="")          # multi-fit → no engine segment
    assert svc.assign_new_sku(p, pai) == "JAKS-INJ-90001"


def test_assign_new_sku_uses_derived_code_when_category_code_blank(db_session):
    db = db_session
    svc = SkuService(db)
    cat = _cat(db, "Engine Parts", code="")          # no explicit code → derive 'ENG'
    pai = _vendor(db, "PAI Industries", "PAI", "9")
    p = _product(db, cat, engine_model="")
    assert svc.assign_new_sku(p, pai) == "JAKS-ENG-90001"


def test_vendor_twin_shares_sequence_with_different_digit(db_session):
    """The owner-locked rule: a 2nd vendor's version of the SAME part is a NEW
    sku that shares the sequence, swapping only the vendor digit."""
    db = db_session
    svc = SkuService(db)
    inj = _cat(db, "Injectors", code="INJ")
    pai = _vendor(db, "PAI Industries", "PAI", "9")
    hhp = _vendor(db, "HHP Parts", "HHP", "3")

    base = _product(db, inj, engine_model="ISX SERIES")
    svc.assign_new_sku(base, pai)
    assert base.sku == "JAKS-ISX-INJ-90001"

    twin = _product(db, inj, engine_model="ISX SERIES")
    svc.assign_twin_sku(twin, base, hhp)
    assert twin.sku == "JAKS-ISX-INJ-30001"             # readable equivalence
    assert twin.part_seq == base.part_seq == 1          # shared sequence
    # a genuinely new part still gets the next free sequence (twins don't burn one)
    other = _product(db, inj, engine_model="ISX SERIES")
    assert svc.assign_new_sku(other, pai) == "JAKS-ISX-INJ-90002"


def test_code_for_category_prefers_explicit_code(db_session):
    db = db_session
    svc = SkuService(db)
    assert svc.code_for_category(_cat(db, "Fuel Pumps", code="FP")) == "FP"
    assert svc.code_for_category(None) == "GEN"


# ── DB: catalog backfill ────────────────────────────────────────────────────────
def _sourced(db, cat, vendor, *, old_sku, engine_model=""):
    """A product with the given old sku and a PREFERRED vendor source (vendor_sku
    left blank so backfill parking is observable)."""
    p = Product(sku=old_sku, title="part " + old_sku, category=cat,
                engine_model=engine_model, is_active=True)
    db.add(p)
    db.flush()
    db.add(ProductVendorSource(product_id=p.id, vendor_id=vendor.id, is_preferred=True,
                               vendor_part_number="VPN" + str(p.id), vendor_sku=""))
    db.flush()
    return p


def test_regenerate_catalog_dry_run_writes_nothing(db_session):
    db = db_session
    inj = _cat(db, "Injectors", code="INJ")
    pai = _vendor(db, "PAI Industries", "PAI", "9")
    p1 = _sourced(db, inj, pai, old_sku="JAKS-PAI-111", engine_model="ISX SERIES")
    p2 = _sourced(db, inj, pai, old_sku="JAKS-PAI-222", engine_model="ISX SERIES")

    summary = SkuService(db).regenerate_catalog(apply=False)

    assert summary["regenerated"] == 2
    assert summary["samples"][0] == {"old": "JAKS-PAI-111", "new": "JAKS-ISX-INJ-90001",
                                     "title": "part JAKS-PAI-111"}
    db.refresh(p1); db.refresh(p2)
    assert p1.sku == "JAKS-PAI-111"          # dry-run wrote NOTHING
    assert p2.sku == "JAKS-PAI-222"
    assert p1.part_seq is None


def test_regenerate_catalog_apply_rewrites_and_parks_old_number(db_session):
    db = db_session
    inj = _cat(db, "Injectors", code="INJ")
    pai = _vendor(db, "PAI Industries", "PAI", "9")
    p1 = _sourced(db, inj, pai, old_sku="JAKS-PAI-111", engine_model="ISX SERIES")
    p2 = _sourced(db, inj, pai, old_sku="JAKS-PAI-222", engine_model="ISX SERIES")

    summary = SkuService(db).regenerate_catalog(apply=True)

    assert summary["regenerated"] == 2
    db.refresh(p1); db.refresh(p2)
    assert p1.sku == "JAKS-ISX-INJ-90001"
    assert p2.sku == "JAKS-ISX-INJ-90002"
    assert (p1.engine_code, p1.category_code, p1.part_seq) == ("ISX", "INJ", 1)
    # the old JAKS-PAI number is parked on the vendor source → still searchable
    src1 = db.query(ProductVendorSource).filter_by(product_id=p1.id).first()
    assert src1.vendor_sku == "JAKS-PAI-111"


def test_regenerate_catalog_skips_no_vendor_and_no_number(db_session):
    db = db_session
    inj = _cat(db, "Injectors", code="INJ")
    nonum = _vendor(db, "Unnumbered Co", "UNN", "")        # vendor_number unset
    p_nosrc = Product(sku="JAKS-PAI-1", title="x", category=inj, is_active=True)
    db.add(p_nosrc); db.flush()
    p_nonum = _sourced(db, inj, nonum, old_sku="JAKS-PAI-2")

    summary = SkuService(db).regenerate_catalog(apply=True)

    assert summary["skipped_no_vendor"] == 1
    assert summary["skipped_no_vendor_number"] == 1
    assert summary["regenerated"] == 0
    db.refresh(p_nosrc); db.refresh(p_nonum)
    assert p_nosrc.sku == "JAKS-PAI-1"          # untouched
    assert p_nonum.sku == "JAKS-PAI-2"
