"""
tests/test_feed_mode_import.py
==============================
P4 feed_mode contract (ERP side).

The scraper now stamps a trailing ``feed_mode`` column on its FULL-IMPORT csv
(constant ``full_import``). When a CSV carries that column the ERP importer must
treat it as AUTHORITATIVE over the header-based detect_format heuristic:

  • feed_mode == 'full_import'    → the existing create/enrich import path.
  • feed_mode == 'pricing_update' → pricing_update_sell (NEVER creates).
  • column ABSENT                 → the existing header-based detect_format
                                    behavior, BYTE-IDENTICAL (today's files have
                                    no feed_mode column).
  • present but contradicts the file shape, or an unrecognized value
                                    → REFUSE the whole file (nothing changed),
                                    mirroring detect_format=='unknown'.

Default-safe: no existing file (no feed_mode column) changes behavior.
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
from app.models.product import Product, ProductVendorSource
from app.models.vendor import Vendor
from app.services.product_import_service import ProductImportService
from tests.conftest import activate, fresh_engine


# Full-import (create/enrich) JAKS-native header — the create path's shape.
_FULL_HEADER = ("pai_part_no,vendor,title,category,engine_make,engine_models,"
                "oem_refs,cost,sell_price,core_charge,is_reman,unit_of_measure,"
                "pack_qty,warranty_years,weight_grams,image_urls,image_files,"
                "superseded_by,in_stock,status,last_updated")
_ROW_PAI = ("040000,PAI,HEAD BOLT,Fasteners,CUMMINS,855,,7.23,10.51,,0,EA,6,2.0,"
            "144,,,,1,active,2026-06-10T00:00:00+00:00")


def _full_csv(*rows: str, feed_mode: str | None = None) -> str:
    """Build a JAKS full-import CSV, optionally appending a feed_mode column."""
    if feed_mode is None:
        header, body = _FULL_HEADER, list(rows)
    else:
        header = _FULL_HEADER + ",feed_mode"
        body = [f"{r},{feed_mode}" for r in rows]
    return header + "\n" + "\n".join(body) + "\n"


def _pricing_csv(rows: list[list[str]], *, feed_mode: str | None = None) -> str:
    """A thin SKU+price refresh feed (NO create/enrich columns), optionally
    carrying a feed_mode column."""
    header = ["sku", "vendor", "our cost", "price"]
    if feed_mode is not None:
        header = header + ["feed_mode"]
        rows = [r + [feed_mode] for r in rows]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    s.add(Vendor(name="PAI Industries", vendor_code="PAI",
                 vendor_number="9", is_active=True))
    s.commit()
    try:
        yield s
    finally:
        s.close()


# ── detect_feed_mode (pure reader) ─────────────────────────────────────────────

def test_detect_feed_mode_absent_is_blank():
    # Today's files carry no feed_mode column → "" (fall back to header heuristic).
    assert ProductImportService.detect_feed_mode(_full_csv(_ROW_PAI)) == ""


def test_detect_feed_mode_reads_full_import():
    text = _full_csv(_ROW_PAI, feed_mode="full_import")
    assert ProductImportService.detect_feed_mode(text) == "full_import"


def test_detect_feed_mode_reads_pricing_update():
    text = _pricing_csv([["040000", "PAI", "9.99", "10.51"]],
                        feed_mode="pricing_update")
    assert ProductImportService.detect_feed_mode(text) == "pricing_update"


# ── (a) Regression: no feed_mode column → unchanged behavior ───────────────────

def test_no_feed_mode_imports_exactly_as_before(db):
    # A file WITHOUT the feed_mode column must behave byte-identically to before:
    # the JAKS full-import create path runs and the product is created.
    text = _full_csv(_ROW_PAI)
    assert ProductImportService.detect_feed_mode(text) == ""
    summary = ProductImportService(db, None).full_import(text, dry_run=False)
    assert "error" not in summary, summary
    assert summary["created"] == 1
    prod = db.query(Product).one()
    assert prod.sku == "JAKS-PAI-040000"


# ── (c) feed_mode='full_import' → create/enrich path ───────────────────────────

def test_full_import_feed_mode_routes_to_create(db):
    text = _full_csv(_ROW_PAI, feed_mode="full_import")
    summary = ProductImportService(db, None).full_import(text, dry_run=False)
    assert "error" not in summary, summary
    assert summary["mode"] == "full_import"
    assert summary["created"] == 1
    assert db.query(Product).count() == 1
    assert db.query(ProductVendorSource).filter_by(vendor_part_number="040000").count() == 1


# ── (b) feed_mode='pricing_update' → never-create path ─────────────────────────

def test_pricing_update_feed_mode_routes_to_pricing(db):
    # First create the product via a normal (no-feed_mode) full import.
    ProductImportService(db, None).full_import(_full_csv(_ROW_PAI), dry_run=False)
    assert db.query(Product).count() == 1
    src = db.query(ProductVendorSource).filter_by(vendor_part_number="040000").one()
    assert float(src.vendor_cost) == 7.23

    # A pricing_update feed reaching full_import must be ROUTED to the never-create
    # pricing path: it updates the cost but creates NOTHING new.
    pricing = _pricing_csv([["040000", "PAI", "9.99", "10.51"]],
                           feed_mode="pricing_update")
    summary = ProductImportService(db, None).full_import(pricing, dry_run=False)
    assert "error" not in summary, summary
    # Routed to pricing_update_sell — its summary marks the pricing mode.
    assert summary["mode"] == "pricing_update"
    assert "created" not in summary or summary.get("created", 0) == 0
    assert summary["costs_updated"] == 1, summary
    assert db.query(Product).count() == 1          # nothing created
    db.refresh(src)
    assert float(src.vendor_cost) == 9.99          # cost refreshed


def test_pricing_update_feed_mode_never_creates_unknown_sku(db):
    # A pricing_update row for a SKU that does not exist creates nothing.
    pricing = _pricing_csv([["999999", "PAI", "1.00", "2.00"]],
                           feed_mode="pricing_update")
    summary = ProductImportService(db, None).full_import(pricing, dry_run=False)
    assert "error" not in summary, summary
    assert summary["mode"] == "pricing_update"
    assert summary["skipped_no_product"] == 1, summary
    assert db.query(Product).count() == 0


# ── (d) Contradiction / unknown → refuse, nothing changed ──────────────────────

def test_pricing_update_on_full_shaped_file_refuses(db):
    # 'pricing_update' declared on a file that carries the full create/enrich
    # columns is a contradiction → refuse the whole file, write nothing.
    text = _full_csv(_ROW_PAI, feed_mode="pricing_update")
    summary = ProductImportService(db, None).full_import(text, dry_run=False)
    assert "error" in summary, summary
    assert summary["created"] == 0
    assert db.query(Product).count() == 0


def test_full_import_on_pricing_shaped_file_refuses(db):
    # 'full_import' declared on a thin pricing-only file (no create/enrich
    # columns) is a contradiction → refuse, write nothing.
    text = _pricing_csv([["040000", "PAI", "9.99", "10.51"]],
                        feed_mode="full_import")
    summary = ProductImportService(db, None).full_import(text, dry_run=False)
    assert "error" in summary, summary
    assert summary["created"] == 0
    assert db.query(Product).count() == 0


def test_unknown_feed_mode_value_refuses(db):
    text = _full_csv(_ROW_PAI, feed_mode="garbage_mode")
    summary = ProductImportService(db, None).full_import(text, dry_run=False)
    assert "error" in summary, summary
    assert summary["created"] == 0
    assert db.query(Product).count() == 0


def test_refusal_writes_nothing_even_when_not_dry_run(db):
    # Belt-and-braces: an explicit non-dry-run refuse still leaves the DB empty.
    text = _full_csv(_ROW_PAI, feed_mode="pricing_update")
    before = db.query(Product).count()
    ProductImportService(db, None).full_import(text, dry_run=False)
    assert db.query(Product).count() == before == 0
