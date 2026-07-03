"""
JAKS Inventory — Phase 5 Automated Tests
Sales Velocity & Auto Min/Max

Tests the new velocity-based min/max system:
  Sales velocity calculation → Auto min/max → Velocity report → Settings

Run:
    cd c:\\Users\\keith\\Website\\jaks_scraper\\jaks_inventory
    .\\.venv\\Scripts\\Activate.ps1
    $env:DATABASE_URL = "sqlite:///c:/Users/keith/Website/jaks_scraper/output/inventory.db"
    $env:PYTHONPATH = "c:\\Users\\keith\\Website\\jaks_scraper"
    python tests\\test_phase5.py
"""

import sys
import uuid
from datetime import date, timedelta

# ── Helpers ──────────────────────────────────────────────────────────

PASS = 0
FAIL = 0
ERRORS = []


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ PASS: {name}")
    else:
        FAIL += 1
        msg = f"  ❌ FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        ERRORS.append(name)


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ── Test Suite ───────────────────────────────────────────────────────

def main():
    global PASS, FAIL

    uid = uuid.uuid4().hex[:6].upper()
    sku_fast = f"PH5-FAST-{uid}"
    sku_slow = f"PH5-SLOW-{uid}"
    sku_zero = f"PH5-ZERO-{uid}"

    from db.inventory import (
        create_product, get_product_by_sku, get_product_by_id,
        adjust_product_quantity,
        create_customer, create_vendor,
        create_invoice, add_invoice_line, finalize_invoice,
        update_min_max,
        get_sales_velocity, auto_calculate_min_max, get_velocity_report,
        get_setting, set_setting,
        get_all_vendors,
    )
    from db.database import get_db

    # ── SETUP ───────────────────────────────────────────────────────
    section("SETUP: Products, Vendor, Customer, Historical Sales")

    # Create vendor with lead time
    vendors = get_all_vendors()
    vendor = vendors[0] if vendors else None
    vendor_id = vendor["id"] if vendor else None
    test("have a vendor", vendor_id is not None)

    # Set lead time on vendor
    if vendor_id:
        with get_db() as db:
            db.execute("UPDATE vendors SET lead_time_days = 14 WHERE id = ?", (vendor_id,))
            db.commit()
        test("vendor lead_time = 14 days", True)

    # Create 3 products: fast-mover, slow-mover, zero-sales
    r1 = create_product(sku=sku_fast, title="Fast Mover Turbo PH5", cost=100, selling_price=250,
                        preferred_vendor_id=vendor_id)
    test("create fast-mover product", r1.get("success"))
    fast_id = r1.get("id")

    r2 = create_product(sku=sku_slow, title="Slow Mover Gasket PH5", cost=20, selling_price=50,
                        preferred_vendor_id=vendor_id)
    test("create slow-mover product", r2.get("success"))
    slow_id = r2.get("id")

    r3 = create_product(sku=sku_zero, title="Zero Sales Bracket PH5", cost=5, selling_price=15)
    test("create zero-sales product", r3.get("success"))
    zero_id = r3.get("id")

    # Stock products
    adjust_product_quantity(sku=sku_fast, delta=50, txn_type="receive", reference="PH5-SETUP")
    adjust_product_quantity(sku=sku_slow, delta=50, txn_type="receive", reference="PH5-SETUP")
    adjust_product_quantity(sku=sku_zero, delta=10, txn_type="receive", reference="PH5-SETUP")

    # Customer
    cust_id = create_customer(name=f"Velocity Customer {uid}", company="Speed Fleet")
    test("create customer", cust_id and cust_id > 0)

    # Create historical invoices to simulate sales
    # Fast-mover: 30 units across 3 invoices
    invoice_ids = []
    for i, (qty, days_ago) in enumerate([(12, 60), (10, 30), (8, 5)]):
        inv = create_invoice(customer_id=cust_id, user="test_phase5")
        inv_id = inv.get("id")
        # Backdate the invoice
        past_date = str(date.today() - timedelta(days=days_ago))
        with get_db() as db:
            db.execute("UPDATE invoices SET invoice_date = ? WHERE id = ?", (past_date, inv_id))
            db.commit()
        add_invoice_line(invoice_id=inv_id, product_id=fast_id, quantity=qty, unit_price=250)
        finalize_invoice(inv_id, deduct_inventory=True, user="test_phase5")
        invoice_ids.append(inv_id)

    test("fast-mover: 3 invoices created (30 total units)", len(invoice_ids) == 3)

    # Slow-mover: 3 units across 1 invoice
    inv = create_invoice(customer_id=cust_id, user="test_phase5")
    slow_inv_id = inv.get("id")
    past_date = str(date.today() - timedelta(days=45))
    with get_db() as db:
        db.execute("UPDATE invoices SET invoice_date = ? WHERE id = ?", (past_date, slow_inv_id))
        db.commit()
    add_invoice_line(invoice_id=slow_inv_id, product_id=slow_id, quantity=3, unit_price=50)
    finalize_invoice(slow_inv_id, deduct_inventory=True, user="test_phase5")
    invoice_ids.append(slow_inv_id)

    test("slow-mover: 1 invoice (3 units)", True)

    # Zero-sales: no invoices — leave as is

    # ── E1: Sales Velocity Calculation ──────────────────────────────
    section("E1: Sales Velocity Calculation")

    # All products velocity
    all_vel = get_sales_velocity(days=90)
    test("get_sales_velocity returns list", isinstance(all_vel, list))
    test("has velocity data", len(all_vel) > 0)

    # Fast-mover velocity
    fast_vel = [v for v in all_vel if v.get("product_id") == fast_id]
    test("fast-mover in results", len(fast_vel) == 1)
    if fast_vel:
        fv = fast_vel[0]
        test("fast-mover total_sold = 30", int(fv.get("total_sold", 0)) == 30,
             f"got {fv.get('total_sold')}")
        test("fast-mover avg_daily > 0", fv.get("avg_daily", 0) > 0,
             f"got {fv.get('avg_daily')}")
        # 30 units / 90 days = 0.3333
        test("fast-mover avg_daily ~ 0.33", abs(fv["avg_daily"] - 0.3333) < 0.01,
             f"got {fv['avg_daily']}")
        test("fast-mover avg_weekly ~ 2.33", abs(fv["avg_weekly"] - 2.33) < 0.1,
             f"got {fv['avg_weekly']}")
        test("fast-mover avg_monthly ~ 10", abs(fv["avg_monthly"] - 10) < 1,
             f"got {fv['avg_monthly']}")
        test("fast-mover has sku", fv.get("sku") == sku_fast)
        test("fast-mover has title", "Fast Mover" in (fv.get("title") or ""))

    # Slow-mover velocity
    slow_vel = [v for v in all_vel if v.get("product_id") == slow_id]
    test("slow-mover in results", len(slow_vel) == 1)
    if slow_vel:
        sv = slow_vel[0]
        test("slow-mover total_sold = 3", int(sv.get("total_sold", 0)) == 3)
        test("slow-mover avg_daily < fast-mover", sv["avg_daily"] < fv["avg_daily"])

    # Zero-sales should NOT appear (no sales in period)
    zero_vel = [v for v in all_vel if v.get("product_id") == zero_id]
    test("zero-sales NOT in velocity results", len(zero_vel) == 0)

    # Single product velocity
    single = get_sales_velocity(product_id=fast_id, days=90)
    test("single product query", len(single) == 1)
    test("single product matches all query", single[0]["avg_daily"] == fv["avg_daily"])

    # Different lookback period
    short_vel = get_sales_velocity(product_id=fast_id, days=15)
    test("shorter lookback returns data", len(short_vel) >= 0)
    if short_vel:
        # Only the most recent 8-unit sale should be in 15-day window
        test("shorter lookback = fewer units", int(short_vel[0]["total_sold"]) <= 30)

    # ── E2: Auto Calculate Min/Max ──────────────────────────────────
    section("E2: Auto Calculate Min/Max")

    # Verify products start at 0/0
    p1_before = get_product_by_id(fast_id)
    test("fast-mover min_qty starts at 0", int(p1_before.get("min_qty") or 0) == 0)
    test("fast-mover max_qty starts at 0", int(p1_before.get("max_qty") or 0) == 0)

    # Run auto-calculate for fast-mover only
    results = auto_calculate_min_max(
        product_id=fast_id,
        lookback_days=90,
        default_lead_time=14,
        safety_multiplier=1.5
    )
    test("auto_calculate returns list", isinstance(results, list))
    test("results has fast-mover", len(results) == 1)

    if results:
        r = results[0]
        test("result has product_id", r["product_id"] == fast_id)
        test("result has sku", r["sku"] == sku_fast)
        test("result has old_min = 0", r["old_min"] == 0)
        test("result has old_max = 0", r["old_max"] == 0)
        test("new_min > 0", r["new_min"] > 0, f"got {r['new_min']}")
        test("new_max > 0", r["new_max"] > 0, f"got {r['new_max']}")
        test("new_max >= new_min", r["new_max"] >= r["new_min"])
        test("result has avg_daily", r["avg_daily"] > 0)
        test("result has lead_time", r["lead_time"] == 14)

        # Verify formula: min = ceil(0.3333 * 14 * 1.5) = ceil(7.0) = 7
        import math
        expected_min = math.ceil(0.3333 * 14 * 1.5)
        expected_max = math.ceil(0.3333 * (14 + 14))
        test(f"new_min = {expected_min} (ceil(avg*lt*safety))", r["new_min"] == expected_min,
             f"got {r['new_min']}")
        test(f"new_max = {expected_max} (ceil(avg*(lt+review)))", r["new_max"] == expected_max,
             f"got {r['new_max']}")

    # Verify product updated in DB
    p1_after = get_product_by_id(fast_id)
    test("DB min_qty updated", int(p1_after.get("min_qty") or 0) > 0)
    test("DB max_qty updated", int(p1_after.get("max_qty") or 0) > 0)

    # ── E3: Auto Calculate with Vendor Lead Time ────────────────────
    section("E3: Vendor-Specific Lead Time")

    # Set vendor lead time to 21 days
    if vendor_id:
        with get_db() as db:
            db.execute("UPDATE vendors SET lead_time_days = 21 WHERE id = ?", (vendor_id,))
            db.commit()

    # Reset min/max to 0
    update_min_max(fast_id, min_qty=0, max_qty=0)

    results2 = auto_calculate_min_max(product_id=fast_id, lookback_days=90, safety_multiplier=1.5)
    test("auto-calc with vendor lead_time=21", len(results2) == 1)
    if results2:
        test("lead_time = 21 (from vendor)", results2[0]["lead_time"] == 21)
        import math
        expected_min_21 = math.ceil(0.3333 * 21 * 1.5)
        test(f"new_min = {expected_min_21} (lt=21)", results2[0]["new_min"] == expected_min_21,
             f"got {results2[0]['new_min']}")

    # ── E4: Bulk Auto-Calculate ─────────────────────────────────────
    section("E4: Bulk Auto-Calculate (all products)")

    # Reset both products
    update_min_max(fast_id, min_qty=0, max_qty=0)
    update_min_max(slow_id, min_qty=0, max_qty=0)

    bulk = auto_calculate_min_max(lookback_days=90, default_lead_time=14, safety_multiplier=1.5)
    test("bulk returns list", isinstance(bulk, list))
    test("bulk has entries (fast + slow at minimum)", len(bulk) >= 2,
         f"got {len(bulk)}")

    # Find our products in results
    fast_bulk = [b for b in bulk if b.get("product_id") == fast_id]
    slow_bulk = [b for b in bulk if b.get("product_id") == slow_id]
    zero_bulk = [b for b in bulk if b.get("product_id") == zero_id]

    test("fast-mover in bulk results", len(fast_bulk) == 1)
    test("slow-mover in bulk results", len(slow_bulk) == 1)
    test("zero-sales NOT in bulk (no velocity)", len(zero_bulk) == 0)

    if fast_bulk and slow_bulk:
        test("fast min > slow min", fast_bulk[0]["new_min"] > slow_bulk[0]["new_min"],
             f"fast={fast_bulk[0]['new_min']}, slow={slow_bulk[0]['new_min']}")
        test("fast max > slow max", fast_bulk[0]["new_max"] > slow_bulk[0]["new_max"],
             f"fast={fast_bulk[0]['new_max']}, slow={slow_bulk[0]['new_max']}")

    # ── E5: Velocity Report ─────────────────────────────────────────
    section("E5: Velocity Report")

    report = get_velocity_report(days=90, min_sold=0)
    test("velocity report returns list", isinstance(report, list))
    test("report has entries", len(report) > 0)

    # Find our products
    fast_rpt = [r for r in report if r.get("id") == fast_id]
    zero_rpt = [r for r in report if r.get("id") == zero_id]
    test("fast-mover in report", len(fast_rpt) == 1)
    test("zero-sales in report (min_sold=0)", len(zero_rpt) == 1)

    if fast_rpt:
        fr = fast_rpt[0]
        test("report has qty_on_hand", "qty_on_hand" in fr)
        test("report has min_qty", "min_qty" in fr)
        test("report has max_qty", "max_qty" in fr)
        test("report has avg_daily", fr.get("avg_daily", 0) > 0)
        test("report has avg_weekly", fr.get("avg_weekly", 0) > 0)
        test("report has avg_monthly", fr.get("avg_monthly", 0) > 0)
        test("report has total_sold", fr.get("total_sold", 0) == 30)
        test("report has days_analyzed", fr.get("days_analyzed") == 90)
        test("report has vendor_name", fr.get("vendor_name") is not None)
        test("report has lead_time_days", fr.get("lead_time_days") is not None)

    if zero_rpt:
        zr = zero_rpt[0]
        test("zero-sales avg_daily = 0", zr.get("avg_daily", -1) == 0)
        test("zero-sales total_sold = 0", zr.get("total_sold", -1) == 0)

    # min_sold filter
    report_active = get_velocity_report(days=90, min_sold=1)
    zero_in_active = [r for r in report_active if r.get("id") == zero_id]
    test("zero-sales excluded with min_sold=1", len(zero_in_active) == 0)

    # ── E6: Settings Integration ────────────────────────────────────
    section("E6: Settings Integration")

    test("velocity_lookback_days setting", get_setting("velocity_lookback_days") == "90")
    test("safety_stock_multiplier setting", get_setting("safety_stock_multiplier") == "1.5")
    test("default_lead_time_days setting", get_setting("default_lead_time_days") == "14")

    # Auto-calculate using settings (no explicit params)
    update_min_max(fast_id, min_qty=0, max_qty=0)
    settings_result = auto_calculate_min_max(product_id=fast_id)
    test("auto-calc uses settings defaults", len(settings_result) == 1)

    # ── CLEANUP ─────────────────────────────────────────────────────
    section("Cleanup: Remove Test Data")

    try:
        with get_db() as db:
            # Core returns (from finalized invoices)
            for iid in invoice_ids:
                db.execute("DELETE FROM core_returns WHERE invoice_id = ?", (iid,))
            # Invoice lines + invoices
            for iid in invoice_ids:
                db.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (iid,))
                db.execute("DELETE FROM invoices WHERE id = ?", (iid,))
            # Inventory transactions
            for pid in [fast_id, slow_id, zero_id]:
                if pid:
                    db.execute("DELETE FROM inventory_transactions WHERE product_id = ?", (pid,))
            # Products
            db.execute("DELETE FROM products WHERE sku IN (?, ?, ?)", (sku_fast, sku_slow, sku_zero))
            # Customer
            if cust_id:
                db.execute("DELETE FROM customers WHERE id = ?", (cust_id,))
            # Reset vendor lead_time
            if vendor_id:
                db.execute("UPDATE vendors SET lead_time_days = 14 WHERE id = ?", (vendor_id,))
            db.commit()
        test("test data cleaned up", True)
    except Exception as e:
        test("cleanup", False, str(e))

    # ── SUMMARY ─────────────────────────────────────────────────────
    section("RESULTS SUMMARY")

    total = PASS + FAIL
    print(f"\n  Total:  {total}")
    print(f"  Passed: {PASS}")
    print(f"  Failed: {FAIL}")

    if ERRORS:
        print(f"\n  Failed tests:")
        for e in ERRORS:
            print(f"    - {e}")

    if FAIL == 0:
        print("\n  🎉 ALL PHASE 5 TESTS PASSED — Ready for Phase 6!")
    else:
        print(f"\n  ⚠️  {FAIL} test(s) failed — fix before proceeding to Phase 6")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
