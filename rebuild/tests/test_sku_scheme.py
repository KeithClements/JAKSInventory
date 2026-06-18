"""
tests/test_sku_scheme.py
========================
SKU generation: default vendor scheme ({prefix}-{vendor_code}-{part#}), the
per-product / settings 'coded' override, the house-brand path, and the
build_vendor_sku helper.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.models.product import Product, ProductCategory
from app.models.vendor import Vendor
from app.services.product_service import ProductService
from app.services.sku_service import build_vendor_sku
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


@pytest.fixture(autouse=True)
def _reset_sku_settings(db):
    """The module shares one engine, so a test that sets sku_prefix/sku_scheme
    would otherwise leak into the next. Reset to defaults before every test →
    order-independent."""
    set_setting_value_db(db, "sku_prefix", "JAKS")
    set_setting_value_db(db, "sku_scheme", "vendor")
    db.commit()
    yield


def _vendor(db, code, *, digit="", private_label=False):
    v = Vendor(name=f"{code} Supply", vendor_code=code, vendor_number=digit,
               private_label=private_label)
    db.add(v); db.commit(); db.refresh(v)
    return v


def _category(db, name="Head Bolts", code="HBLT"):
    c = ProductCategory(name=name, code=code, level=1, is_active=True)
    db.add(c); db.commit(); db.refresh(c)
    return c


# ── build_vendor_sku (pure) ──────────────────────────────────────────────────

def test_build_vendor_sku():
    assert build_vendor_sku("JAKS", "PAI", "340097") == "JAKS-PAI-340097"
    assert build_vendor_sku("jaks", "pai", "abc12") == "JAKS-PAI-ABC12"   # uppercased
    assert build_vendor_sku("JAKS", "PAI", "040000") == "JAKS-PAI-040000"  # leading zeros kept
    assert build_vendor_sku("JAKS", "", "340097") == "JAKS-340097"         # no vendor code


# ── default vendor scheme ────────────────────────────────────────────────────

def test_create_product_default_vendor_scheme(db):
    v = _vendor(db, "PAI")
    p = ProductService(db).create_product(
        {"vendor_id": v.id, "vendor_part_number": "340097", "title": "Head Bolt"}
    )
    assert p.sku == "JAKS-PAI-340097"


def test_default_respects_sku_prefix_setting(db):
    set_setting_value_db(db, "sku_prefix", "AXLE"); db.commit()
    v = _vendor(db, "IMB")
    p = ProductService(db).create_product(
        {"vendor_id": v.id, "vendor_part_number": "55-1", "title": "Gasket"}
    )
    assert p.sku == "AXLE-IMB-55-1"


# ── per-product coded override ───────────────────────────────────────────────

def test_per_product_coded_override(db):
    v = _vendor(db, "CAT", digit="9")
    cat = _category(db)
    p = ProductService(db).create_product({
        "vendor_id": v.id, "vendor_part_number": "5001", "title": "Head Bolt",
        "category_id": cat.id, "engine_make": "CAT", "engine_model": "C15",
        "sku_scheme": "coded",
    })
    # Coded scheme: NOT the vendor part-number form; sequenced + part_seq stamped.
    assert p.sku != "JAKS-CAT-5001"
    assert p.sku.startswith("JAKS-")
    assert p.sku.endswith("90001")        # vendor digit 9 + first sequence
    assert p.part_seq == 1


def test_coded_requires_vendor_digit(db):
    v = _vendor(db, "NOD")   # no vendor_number digit
    with pytest.raises(ValueError):
        ProductService(db).create_product({
            "vendor_id": v.id, "vendor_part_number": "1", "title": "x",
            "sku_scheme": "coded",
        })


def test_settings_default_coded(db):
    set_setting_value_db(db, "sku_scheme", "coded"); db.commit()
    v = _vendor(db, "PAI", digit="9")
    cat = _category(db, name="Filters", code="FLT")
    p = ProductService(db).create_product({   # no per-product override → uses setting
        "vendor_id": v.id, "vendor_part_number": "700", "title": "Filter",
        "category_id": cat.id,
    })
    assert p.sku != "JAKS-PAI-700"
    assert p.part_seq is not None


# ── house brand unaffected ───────────────────────────────────────────────────

def test_house_brand_uses_owner_number(db):
    v = _vendor(db, "PAI")
    p = ProductService(db).create_product({
        "vendor_id": v.id, "vendor_part_number": "X1", "title": "Private Reman",
        "is_house_brand": True, "jaks_product_number": "HB-100",
    })
    assert p.sku == "HB-100"


def test_house_brand_without_owner_number_masks_vendor_code(db):
    """is_house_brand with NO owner-typed number → hide the vendor code, keep the
    part # under the house prefix: {prefix}-{prefix}-{part#} (JAKS-JAKS-10R1273)."""
    v = _vendor(db, "DFT")
    p = ProductService(db).create_product({
        "vendor_id": v.id, "vendor_part_number": "10R1273", "title": "Reman pump",
        "is_house_brand": True,
    })
    assert p.sku == "JAKS-JAKS-10R1273"
    assert p.is_house_brand is True


# ── private-label VENDOR (DFT / Migao) ───────────────────────────────────────

def test_private_label_vendor_masks_sku_and_flags_house_brand(db):
    """A vendor flagged private_label → ALL its products are house brand and the
    SKU hides the vendor code (uses the house prefix instead)."""
    v = _vendor(db, "DFT", private_label=True)
    p = ProductService(db).create_product(
        {"vendor_id": v.id, "vendor_part_number": "pl5001", "title": "Reman"}
    )
    assert p.sku == "JAKS-JAKS-PL5001"      # not JAKS-DFT-…
    assert p.is_house_brand is True


def test_create_path_strips_stray_prefix_default(db):
    """A typed Vendor Part # that already carries a JAKS-<code>- prefix must not
    double up: JAKS-PAI-778899 stays JAKS-PAI-778899 (not JAKS-PAI-JAKS-PAI-…)."""
    v = _vendor(db, "PAI")
    p = ProductService(db).create_product(
        {"vendor_id": v.id, "vendor_part_number": "JAKS-PAI-778899", "title": "Bolt"}
    )
    assert p.sku == "JAKS-PAI-778899"


def test_create_path_strips_stray_prefix_house_brand(db):
    v = _vendor(db, "DFT", private_label=True)
    p = ProductService(db).create_product(
        {"vendor_id": v.id, "vendor_part_number": "JAKS-DFT-99X", "title": "x"}
    )
    assert p.sku == "JAKS-JAKS-99X"        # not JAKS-JAKS-JAKS-DFT-99X


def test_private_label_collision_raises_clear_message(db):
    """Two private-label vendors with the same bare part# collapse to one masked
    SKU; the second raises advice that does NOT say 'mark this part private-label'
    (it already is) — only to give a distinct JAKS Product #."""
    v1 = _vendor(db, "DFT", private_label=True)
    v2 = _vendor(db, "MIG", private_label=True)
    ProductService(db).create_product(
        {"vendor_id": v1.id, "vendor_part_number": "COLL1", "title": "a"})
    with pytest.raises(ValueError) as ei:
        ProductService(db).create_product(
            {"vendor_id": v2.id, "vendor_part_number": "COLL1", "title": "b"})
    msg = str(ei.value)
    assert "distinct JAKS Product #" in msg
    assert "mark this part private-label" not in msg


def test_private_label_vendor_respects_prefix_setting(db):
    set_setting_value_db(db, "sku_prefix", "AXLE"); db.commit()
    v = _vendor(db, "MIG", private_label=True)
    p = ProductService(db).create_product(
        {"vendor_id": v.id, "vendor_part_number": "55-1", "title": "Gasket"}
    )
    assert p.sku == "AXLE-AXLE-55-1"


def test_private_label_vendor_still_honors_owner_number(db):
    """Even under a private-label vendor, an explicit JAKS Product # wins."""
    v = _vendor(db, "DFT", private_label=True)
    p = ProductService(db).create_product({
        "vendor_id": v.id, "vendor_part_number": "10r1273", "title": "Reman",
        "jaks_product_number": "DOM-9001",
    })
    assert p.sku == "DOM-9001"
    assert p.is_house_brand is True
