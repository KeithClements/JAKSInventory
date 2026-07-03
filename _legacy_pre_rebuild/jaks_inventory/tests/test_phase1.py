"""
JAKS Inventory — Phase 1 Automated Tests
Products & Vendors — Small Win Testing

Run:
    cd c:\\Users\\keith\\Website\\jaks_scraper\\jaks_inventory
    .\\.venv\\Scripts\\Activate.ps1
    $env:DATABASE_URL = "sqlite:///c:/Users/keith/Website/jaks_scraper/output/inventory.db"
    $env:PYTHONPATH = "c:\\Users\\keith\\Website\\jaks_scraper"
    python tests\\test_phase1.py
"""

import sys
import uuid

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

    # Generate a unique SKU so tests don't collide with existing data
    uid = uuid.uuid4().hex[:6].upper()
    test_sku = f"TEST-PH1-{uid}"

    section("Phase 1: Products & Vendors")

    # ── A1: Create Product ──────────────────────────────────────────
    section("A1: Create Product")
    try:
        from db.inventory import create_product
        result = create_product(
            sku=test_sku,
            title="Test Water Pump - Phase 1",
            category="Water Pumps",
            manufacturer="PAI Industries",
            condition="New Engine Parts",
            cost=225.00,
            selling_price=450.00,
            core_charge=150.00,
            engine="ISX 15",
            oem_manufacturer="Cummins",
            brief_description="Water Pump",
            oem_part_number="3800984",
            xref_part_number="681800E",
            invoice_description="Cummins ISX 15 Water Pump 3800984 - PAI Industries 681800E",
        )
        test("create_product returns success", result.get("success") is True, str(result))
        test("create_product returns id", isinstance(result.get("id"), int), str(result))
        product_id = result.get("id")
    except Exception as e:
        test("create_product does not crash", False, str(e))
        product_id = None

    # ── A2: SKU Unique Constraint ───────────────────────────────────
    section("A2: SKU Unique Constraint")
    try:
        dup_result = create_product(sku=test_sku, title="Duplicate")
        test("duplicate SKU returns error", dup_result.get("success") is False, str(dup_result))
        test("error message mentions SKU", "already exists" in str(dup_result.get("error", "")), str(dup_result))
    except Exception as e:
        test("duplicate SKU handled gracefully", False, str(e))

    # ── A3: Retrieve Product ────────────────────────────────────────
    section("A3: Retrieve Product by SKU")
    try:
        from db.inventory import get_product_by_sku
        product = get_product_by_sku(test_sku)
        test("product found by SKU", product is not None)
        if product:
            test("SKU matches", product.get("sku") == test_sku)
            test("title matches", product.get("title") == "Test Water Pump - Phase 1")
            test("manufacturer matches", product.get("manufacturer") == "PAI Industries")
            test("cost matches", float(product.get("cost", 0)) == 225.00)
            test("selling_price matches", float(product.get("selling_price", 0)) == 450.00)
            test("core_charge matches", float(product.get("core_charge", 0)) == 150.00)
            test("condition matches", product.get("condition") == "New Engine Parts")
            test("engine matches", product.get("engine") == "ISX 15")
            test("oem_manufacturer matches", product.get("oem_manufacturer") == "Cummins")
            test("brief_description matches", product.get("brief_description") == "Water Pump")
            test("oem_part_number matches", product.get("oem_part_number") == "3800984")
            test("xref_part_number matches", product.get("xref_part_number") == "681800E")
            test("invoice_description matches",
                 product.get("invoice_description") == "Cummins ISX 15 Water Pump 3800984 - PAI Industries 681800E")
    except Exception as e:
        test("get_product_by_sku does not crash", False, str(e))

    # ── A4: Update Product ──────────────────────────────────────────
    section("A4: Update Product")
    try:
        from db.inventory import update_product
        if product_id:
            ok = update_product(product_id, selling_price=499.99, title="Updated Water Pump - Phase 1")
            test("update_product returns True", ok is True)

            updated = get_product_by_sku(test_sku)
            test("selling_price updated", float(updated.get("selling_price", 0)) == 499.99)
            test("title updated", updated.get("title") == "Updated Water Pump - Phase 1")
            test("manufacturer unchanged", updated.get("manufacturer") == "PAI Industries")
        else:
            test("update_product (skipped — no product_id)", False, "Create failed")
    except Exception as e:
        test("update_product does not crash", False, str(e))

    # ── A5: Browse Products ─────────────────────────────────────────
    section("A5: Browse Products")
    try:
        from db.inventory import browse_products
        all_products = browse_products(limit=100)
        test("browse_products returns list", isinstance(all_products, list))
        test("browse_products has results", len(all_products) > 0, f"got {len(all_products)}")

        # Search for our test product
        search_results = browse_products(search="Phase 1", limit=10)
        test("search finds test product", any(p.get("sku") == test_sku for p in search_results),
             f"got {len(search_results)} results")

        # Filter by manufacturer
        mfr_results = browse_products(manufacturer="PAI Industries", limit=100)
        test("manufacturer filter works", all(p.get("manufacturer") == "PAI Industries" for p in mfr_results))
    except Exception as e:
        test("browse_products does not crash", False, str(e))

    # ── A6: Get All Vendors ─────────────────────────────────────────
    section("A6: Vendor List")
    try:
        from db.inventory import get_all_vendors
        vendors = get_all_vendors()
        test("get_all_vendors returns list", isinstance(vendors, list))
        test("vendors have data", len(vendors) > 0, f"got {len(vendors)} vendors")
        if vendors:
            v = vendors[0]
            test("vendor has name", "name" in v)
            test("vendor has id", "id" in v)
    except Exception as e:
        test("get_all_vendors does not crash", False, str(e))

    # ── A7: Get All Manufacturers ───────────────────────────────────
    section("A7: Manufacturer List")
    try:
        from db.inventory import get_all_manufacturers
        mfrs = get_all_manufacturers()
        test("get_all_manufacturers returns list", isinstance(mfrs, list))
        test("manufacturers have data", len(mfrs) > 0, f"got {len(mfrs)}")
        test("PAI Industries in list", "PAI Industries" in mfrs, str(mfrs))
    except Exception as e:
        test("get_all_manufacturers does not crash", False, str(e))

    # ── A8: Get All Categories ──────────────────────────────────────
    section("A8: Category List")
    try:
        from db.inventory import get_all_categories
        cats = get_all_categories()
        test("get_all_categories returns list", isinstance(cats, list))
        test("categories have data", len(cats) > 0, f"got {len(cats)}")
    except Exception as e:
        test("get_all_categories does not crash", False, str(e))

    # ── A9: Engine Manufacturers ────────────────────────────────────
    section("A9: Engine Manufacturers")
    try:
        from db.inventory import get_all_engine_manufacturers
        eng_mfrs = get_all_engine_manufacturers()
        test("get_all_engine_manufacturers returns list", isinstance(eng_mfrs, list))
        # Our test product added "Cummins" as oem_manufacturer
        test("Cummins in engine manufacturers", "Cummins" in eng_mfrs, str(eng_mfrs))
    except Exception as e:
        test("get_all_engine_manufacturers does not crash", False, str(e))

    # ── A10: Engine Models ──────────────────────────────────────────
    section("A10: Engine Models")
    try:
        from db.inventory import get_all_engine_models
        eng_models = get_all_engine_models()
        test("get_all_engine_models returns list", isinstance(eng_models, list))
        test("ISX 15 in engine models", "ISX 15" in eng_models, str(eng_models))
    except Exception as e:
        test("get_all_engine_models does not crash", False, str(e))

    # ── Cleanup ─────────────────────────────────────────────────────
    section("Cleanup: Remove Test Product")
    try:
        from db.database import get_db
        with get_db() as db:
            db.execute("DELETE FROM products WHERE sku = ?", (test_sku,))
            db.commit()
            db.execute("SELECT COUNT(*) FROM products WHERE sku = ?", (test_sku,))
            remaining = db.fetchone()[0]
            test("test product cleaned up", remaining == 0)
    except Exception as e:
        test("cleanup does not crash", False, str(e))

    # ── Summary ─────────────────────────────────────────────────────
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
        print("\n  🎉 ALL PHASE 1 TESTS PASSED — Ready for Phase 2!")
    else:
        print(f"\n  ⚠️  {FAIL} test(s) failed — fix before proceeding to Phase 2")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
