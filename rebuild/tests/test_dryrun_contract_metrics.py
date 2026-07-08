"""
tests/test_dryrun_contract_metrics.py
=====================================
The CROSS-REPO dry-run safety contract: pricing_update_sell now enriches its
summary with the blast-radius magnitude metrics the scraper gates its push on,
and scripts.import_and_push surfaces them on a `--json` dry run as ONE
"RESULT_JSON: {…}" line (+ a `--new-products-out` no-match SKU CSV).

Contract (must stay byte-for-byte in sync with the scraper lane):
    RESULT_JSON: {"matched":N,"prices_updated":N,"costs_updated":N,
      "availability_updated":N,"out_of_stock_flagged":N,
      "discontinued_deactivated":N,"reactivation_suggested":N,
      "skipped_no_product":N,"total_rows":N,"price_drop_max_pct":F,
      "price_rise_max_pct":F,"reprice_rows":N,"cost_rise_max_pct":F,
      "cost_anomaly_rows":N,"skipped_price_locked":N}
    (skipped_price_locked appended 2026-07-01 — additive at the END, existing
    keys never reordered; the scraper tolerates extra keys.)

Metric definitions exercised here:
  • reprice_rows        — rows whose sell price moves by >5%
  • price_drop_max_pct  — largest single DOWN move, positive percent
  • price_rise_max_pct  — largest single UP move, positive percent
  • cost_anomaly_rows   — rows whose NEW cost is > 3× the current cost
                          (list-price-scraped-as-cost class)
  • cost_rise_max_pct   — largest single cost increase percent
  • skipped_skus        — the no-match SKUs (capped), routed to onboarding
All computed in dry-run too (the underlying counts already increment there).
"""
from __future__ import annotations

import csv
import io
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as appdb
import app.models  # noqa: F401 — register all models
from app.constants import ProductStatus
from app.models.product import Product, ProductVendorSource
from app.models.vendor import Vendor
from app.services.product_import_service import ProductImportService
from tests.conftest import activate, fresh_engine


# Mirror the scraper Shopify export header (kept identical to
# test_r3_pricing_update_sell.py so a format drift is caught in both places).
_HEADER = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value", "Variant SKU", "Variant Grams",
    "Variant Inventory Tracker", "Variant Inventory Qty",
    "Variant Inventory Policy", "Variant Fulfillment Service",
    "Variant Price", "Variant Compare At Price",
    "Variant Requires Shipping", "Variant Taxable", "Variant Barcode",
    "Image Src", "Image Position", "Image Alt Text", "Status",
]

# The exact contract key set (and order) the scraper lane parses.
_CONTRACT_KEYS = [
    "matched", "prices_updated", "costs_updated", "availability_updated",
    "out_of_stock_flagged", "discontinued_deactivated", "reactivation_suggested",
    "skipped_no_product", "total_rows", "price_drop_max_pct", "price_rise_max_pct",
    "reprice_rows", "cost_rise_max_pct", "cost_anomaly_rows",
    "skipped_price_locked",
    # Appended 2026-07-06 — its PRESENCE tells the scraper the per-row drop hold
    # is active, so it no longer aborts the push on one extreme crater.
    "held_price_drop",
]


def _row(**kw):
    d = {h: "" for h in _HEADER}
    d.update(kw)
    return [d[h] for h in _HEADER]


def _price_row(sku, price, compare="", *, handle=None):
    return _row(Handle=(handle or sku.lower()),
                Title="Part", Type="ENGINE PARTS", Status="active",
                **{"Variant SKU": sku, "Variant Price": str(price),
                   "Variant Compare At Price": str(compare)})


def _csv_with_cost(rows_with_cost):
    """rows_with_cost: list of (sku, price, cost). Builds the Shopify header +
    an 'Our Cost' column — the two-vendor scraper export shape."""
    header = _HEADER + ["Our Cost"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for sku, price, cost in rows_with_cost:
        w.writerow(_price_row(sku, price) + [cost])
    return buf.getvalue()


def _csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _make_product(db, sku, *, price=10.00, cost=4.50):
    p = Product(sku=sku, title=sku, description=sku,
                price_override=price, cost=cost,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _seed_pai_source(db, product_id, *, vendor_cost):
    v = db.query(Vendor).filter(Vendor.vendor_code == "PAI").first()
    if v is None:
        v = Vendor(name="PAI Industries", vendor_code="PAI",
                   vendor_number="9", is_active=True)
        db.add(v); db.commit(); db.refresh(v)
    src = ProductVendorSource(product_id=product_id, vendor_id=v.id,
                              vendor_cost=vendor_cost, is_active=True,
                              is_preferred=True)
    db.add(src); db.commit()
    return v, src


# ── Service-side metric enrichment ────────────────────────────────────────────

def test_summary_carries_full_contract_key_set(db):
    """Every contract key is present in the summary, even on a trivial run —
    the scraper must never see a missing key."""
    _make_product(db, "JAKS-PAI-100", price=10.00)
    s = ProductImportService(db, None).pricing_update_sell(
        _csv([_price_row("JAKS-PAI-100", "10.00")]), dry_run=True)
    for k in _CONTRACT_KEYS:
        assert k in s, f"contract key {k} missing from summary"
    assert s["total_rows"] == 1


def test_cost_anomaly_row_flagged_in_dry_run(db):
    """A NEW cost > 3× the current cost (the list-price-scraped-as-cost class)
    increments cost_anomaly_rows — and does so under dry-run, where nothing is
    written. A normal row alongside it does NOT inflate the count."""
    p_norm = _make_product(db, "JAKS-PAI-200", price=10.00)
    p_bad = _make_product(db, "JAKS-PAI-201", price=10.00)
    _seed_pai_source(db, p_norm.id, vendor_cost=4.00)
    _, bad_src = _seed_pai_source(db, p_bad.id, vendor_cost=4.00)
    svc = ProductImportService(db, None)

    text = _csv_with_cost([
        ("JAKS-PAI-200", "10.00", "4.40"),     # +10% cost — normal
        ("JAKS-PAI-201", "10.00", "20.00"),    # 5× cost — anomaly
    ])
    s = svc.pricing_update_sell(text, dry_run=True)
    assert s["cost_anomaly_rows"] == 1
    # largest cost increase = (20-4)/4 = 400%
    assert s["cost_rise_max_pct"] == pytest.approx(400.0, abs=0.1)
    db.refresh(bad_src)
    assert bad_src.vendor_cost == 4.00, "dry-run must not write the anomalous cost"


def test_big_price_drop_sets_drop_max_and_reprice_count(db):
    """A large sell-price DROP shows up as a positive price_drop_max_pct and
    counts toward reprice_rows (>5% move); a tiny ±change does neither."""
    p_drop = _make_product(db, "JAKS-PAI-300", price=100.00)
    p_flat = _make_product(db, "JAKS-PAI-301", price=100.00)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-300", "40.00"),   # -60% drop
              _price_row("JAKS-PAI-301", "102.00")]),  # +2% — under the 5% rail
        dry_run=True)
    assert s["price_drop_max_pct"] == pytest.approx(60.0, abs=0.1)
    assert s["price_rise_max_pct"] == 0.0
    assert s["reprice_rows"] == 1   # only the -60% row clears >5%


def test_max_drop_pct_holds_extreme_drop_applies_the_rest(db):
    """The per-row DROP hold (2026-07-06): with max_drop_pct=60, a row that would
    crater >60% is HELD (price NOT written, flagged), while a moderate drop and a
    rise in the SAME feed still apply. Cost on a held row still applies. This is
    what lets one bad part stop aborting the whole catalog correction."""
    p_crater = _make_product(db, "JAKS-PAI-600", price=100.00)
    p_moderate = _make_product(db, "JAKS-PAI-601", price=100.00)
    p_rise = _make_product(db, "JAKS-PAI-602", price=100.00)
    _seed_pai_source(db, p_crater.id, vendor_cost=50.00)
    svc = ProductImportService(db, None)

    text = _csv_with_cost([
        ("JAKS-PAI-600", "3.00", "40.00"),     # -97% -> HELD (but cost applies)
        ("JAKS-PAI-601", "75.00", ""),         # -25% -> applies
        ("JAKS-PAI-602", "130.00", ""),        # +30% rise -> applies (never held)
    ])
    s = svc.pricing_update_sell(text, dry_run=False, max_drop_pct=60.0)

    assert s["held_price_drop"] == 1
    assert s["held_price_drop_rows"][0]["sku"] == "JAKS-PAI-600"
    assert s["held_price_drop_rows"][0]["drop_pct"] == pytest.approx(97.0, abs=0.1)
    db.refresh(p_crater); db.refresh(p_moderate); db.refresh(p_rise)
    assert p_crater.price_override == 100.00     # HELD — not applied
    assert p_moderate.price_override == 75.00    # moderate drop applied
    assert p_rise.price_override == 130.00       # rise applied
    # cost on the held row STILL applied (independent field, like below-cost gate)
    src = db.query(ProductVendorSource).filter(
        ProductVendorSource.product_id == p_crater.id).first()
    assert src.vendor_cost == 40.00
    # blast-radius metric still reports the TRUE scraped move even though held
    assert s["price_drop_max_pct"] == pytest.approx(97.0, abs=0.1)


def test_max_drop_pct_none_applies_everything(db):
    """Without max_drop_pct (the default), nothing is held — a big drop applies,
    same as before this feature. held_price_drop stays 0."""
    p = _make_product(db, "JAKS-PAI-610", price=100.00)
    svc = ProductImportService(db, None)
    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-610", "3.00")]), dry_run=False)
    assert s["held_price_drop"] == 0
    db.refresh(p)
    assert p.price_override == 3.00


def test_write_held_review_csv(tmp_path):
    """_write_held_review writes the 4-column review CSV the owner acts on."""
    from scripts.import_and_push import _write_held_review
    out = tmp_path / "held.csv"
    _write_held_review(str(out), [
        {"sku": "JAKS-PAI-600", "old_price": 100.0, "new_price": 3.0, "drop_pct": 97.0},
    ])
    rows = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == ["sku", "old_price", "new_price", "drop_pct"]
    assert rows[1] == ["JAKS-PAI-600", "100.0", "3.0", "97.0"]


def test_price_rise_max_tracks_largest_up_move(db):
    p1 = _make_product(db, "JAKS-PAI-400", price=10.00)
    p2 = _make_product(db, "JAKS-PAI-401", price=10.00)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-400", "13.00"),   # +30%
              _price_row("JAKS-PAI-401", "10.80")]),  # +8%
        dry_run=True)
    assert s["price_rise_max_pct"] == pytest.approx(30.0, abs=0.1)
    assert s["price_drop_max_pct"] == 0.0
    assert s["reprice_rows"] == 2   # both clear >5%


def test_skipped_skus_collected_for_onboarding(db):
    """SKUs that match no ERP product land in summary['skipped_skus'] (capped)
    so import_and_push can route them to onboarding."""
    _make_product(db, "JAKS-PAI-500", price=10.00)
    svc = ProductImportService(db, None)

    s = svc.pricing_update_sell(
        _csv([_price_row("JAKS-PAI-500", "11.00"),
              _price_row("JAKS-PAI-NOPE-1", "9.00"),
              _price_row("JAKS-PAI-NOPE-2", "9.00")]),
        dry_run=True)
    assert s["skipped_no_product"] == 2
    assert s["skipped_skus"] == ["JAKS-PAI-NOPE-1", "JAKS-PAI-NOPE-2"]


# ── Script-side RESULT_JSON line + new-products CSV ───────────────────────────

def test_result_json_line_shape_and_keys():
    """_result_json emits the 'RESULT_JSON: ' prefix + one JSON object with the
    EXACT contract key set; missing numerics default to 0; total_rows falls back
    to 'rows'."""
    from scripts.import_and_push import _result_json
    line = _result_json({
        "matched": 3, "prices_updated": 2, "costs_updated": 1,
        "rows": 7, "reprice_rows": 1, "cost_anomaly_rows": 1,
        "price_drop_max_pct": 60.0, "cost_rise_max_pct": 400.0,
        # availability_*, reactivation_suggested, etc. intentionally absent
    })
    assert line.startswith("RESULT_JSON: ")
    obj = json.loads(line[len("RESULT_JSON: "):])
    assert sorted(obj.keys()) == sorted(_CONTRACT_KEYS)
    assert obj["matched"] == 3
    assert obj["total_rows"] == 7              # fell back to "rows"
    assert obj["availability_updated"] == 0    # missing → 0
    assert obj["reactivation_suggested"] == 0
    assert obj["price_drop_max_pct"] == 60.0
    assert obj["price_rise_max_pct"] == 0.0    # missing float → 0.0
    assert obj["cost_anomaly_rows"] == 1
    assert obj["held_price_drop"] == 0         # missing → 0


def test_write_new_products_csv(tmp_path):
    """_write_new_products writes a one-column 'sku' CSV the scraper reads."""
    from scripts.import_and_push import _write_new_products
    out = tmp_path / "new_products.csv"
    _write_new_products(str(out), ["JAKS-PAI-NOPE-1", "JAKS-PAI-NOPE-2"])
    rows = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == ["sku"]
    assert rows[1] == ["JAKS-PAI-NOPE-1"]
    assert rows[2] == ["JAKS-PAI-NOPE-2"]


def test_opt_value_parses_space_form():
    """--new-products-out <path> value parsing, and a bare positional that is
    NOT mistaken for the option's value."""
    from scripts.import_and_push import _opt_value
    argv = ["feed.csv", "--json", "--new-products-out", "out.csv"]
    assert _opt_value(argv, "--new-products-out") == "out.csv"
    assert _opt_value(argv, "--missing") is None
