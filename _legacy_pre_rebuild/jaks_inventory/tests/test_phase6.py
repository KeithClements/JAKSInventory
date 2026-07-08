"""
JAKS Inventory — Phase 6 Automated Tests
SKU Prefix Management, Auto-SKU Generation, Duplicate Detection

Tests the new backend functions for UI polish features:
  Prefix CRUD → Auto-SKU → Vendor prefix lookup → Duplicate detection

Run:
    cd c:\\Users\\keith\\Website\\jaks_scraper\\jaks_inventory
    .\\.venv\\Scripts\\Activate.ps1
    $env:DATABASE_URL = "sqlite:///c:/Users/keith/Website/jaks_scraper/output/inventory.db"
    $env:PYTHONPATH = "c:\\Users\\keith\\Website\\jaks_scraper"
    python tests\\test_phase6.py
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

    uid = uuid.uuid4().hex[:6].upper()
    test_prefix = f"T{uid[:3]}"  # Short prefix like TABC

    from db.inventory import (
        create_product, get_product_by_sku,
        get_all_vendors,
        create_sku_prefix, get_sku_prefixes, update_sku_prefix, delete_sku_prefix,
        generate_next_sku, get_sku_prefix_for_vendor,
        find_similar_products,
    )
    from db.database import get_db

    # ── SETUP ───────────────────────────────────────────────────────
    section("SETUP")

    vendors = get_all_vendors()
    vendor = vendors[0] if vendors else None
    vendor_id = vendor["id"] if vendor else None
    vendor_name = vendor.get("name", "Unknown") if vendor else "No vendor"
    test("have a vendor", vendor_id is not None, vendor_name)

    # ── F1: Create SKU Prefix ───────────────────────────────────────
    section("F1: Create SKU Prefix")

    prefix_id = create_sku_prefix(
        prefix=test_prefix,
        manufacturer="PAI Industries",
        vendor_id=vendor_id,
        description="Test prefix for Phase 6"
    )
    test("create_sku_prefix returns id", prefix_id is not None and prefix_id > 0)

    # Create a second prefix
    prefix2 = f"X{uid[:3]}"
    prefix2_id = create_sku_prefix(prefix=prefix2, manufacturer="Cummins")
    test("create second prefix (no vendor)", prefix2_id is not None and prefix2_id > 0)

    # ── F2: List SKU Prefixes ───────────────────────────────────────
    section("F2: List SKU Prefixes")

    all_prefixes = get_sku_prefixes()
    test("get_sku_prefixes returns list", isinstance(all_prefixes, list))
    test("has prefixes", len(all_prefixes) >= 2)

    found = [p for p in all_prefixes if p.get("id") == prefix_id]
    test("our prefix in list", len(found) == 1)
    if found:
        test("prefix stored uppercase", found[0].get("prefix") == test_prefix.upper())
        test("manufacturer saved", found[0].get("manufacturer") == "PAI Industries")
        test("vendor_id saved", found[0].get("vendor_id") == vendor_id)
        test("vendor_name joined", found[0].get("vendor_name") is not None)
        test("description saved", found[0].get("description") == "Test prefix for Phase 6")

    # Filter by vendor
    vendor_prefixes = get_sku_prefixes(vendor_id=vendor_id)
    test("filter by vendor", all(p.get("vendor_id") == vendor_id for p in vendor_prefixes))
    test("our prefix in vendor filter", any(p.get("id") == prefix_id for p in vendor_prefixes))

    # Filter by manufacturer
    mfr_prefixes = get_sku_prefixes(manufacturer="PAI Industries")
    test("filter by manufacturer", all(p.get("manufacturer") == "PAI Industries" for p in mfr_prefixes))

    # ── F3: Update SKU Prefix ───────────────────────────────────────
    section("F3: Update SKU Prefix")

    ok = update_sku_prefix(prefix_id, description="Updated description")
    test("update returns True", ok is True)

    updated = [p for p in get_sku_prefixes() if p.get("id") == prefix_id]
    test("description updated", updated[0].get("description") == "Updated description" if updated else False)

    # No-op update
    noop = update_sku_prefix(prefix_id)
    test("no-op update returns False", noop is False)

    # ── F4: Auto-SKU Generation ─────────────────────────────────────
    section("F4: Auto-SKU Generation")

    sku1 = generate_next_sku(test_prefix)
    test("generate_next_sku returns string", isinstance(sku1, str))
    test("SKU format PREFIX-000001", sku1 == f"{test_prefix.upper()}-000001", sku1)

    sku2 = generate_next_sku(test_prefix)
    test("second SKU = PREFIX-000002", sku2 == f"{test_prefix.upper()}-000002", sku2)

    sku3 = generate_next_sku(test_prefix)
    test("third SKU = PREFIX-000003", sku3 == f"{test_prefix.upper()}-000003", sku3)

    # Different prefix gets its own sequence
    sku_other = generate_next_sku(prefix2)
    test("different prefix starts at 1", sku_other == f"{prefix2.upper()}-000001", sku_other)

    # Case insensitive
    sku_lower = generate_next_sku(test_prefix.lower())
    test("case insensitive (PREFIX-000004)", sku_lower == f"{test_prefix.upper()}-000004", sku_lower)

    # ── F5: Vendor Prefix Lookup ────────────────────────────────────
    section("F5: Vendor Prefix Lookup")

    vp = get_sku_prefix_for_vendor(vendor_id) if vendor_id else None
    test("get_sku_prefix_for_vendor returns prefix", vp is not None)
    test("returns our prefix string", vp == test_prefix.upper(), vp)

    # Non-existent vendor
    no_prefix = get_sku_prefix_for_vendor(99999)
    test("non-existent vendor returns None", no_prefix is None)

    # ── F6: Duplicate Detection — Setup Test Products ───────────────
    section("F6: Duplicate Detection")

    # Create a set of products that could be duplicates
    dup_sku_1 = f"DUP-WP-{uid}"
    dup_sku_2 = f"DUP-WP2-{uid}"
    dup_sku_3 = f"DUP-OP-{uid}"

    r1 = create_product(
        sku=dup_sku_1,
        title="Water Pump for Cummins ISX 15",
        oem_part_number="4089908",
        xref_part_number="681800E",
        manufacturer="PAI Industries"
    )
    dup_prod1_id = r1.get("id")

    r2 = create_product(
        sku=dup_sku_2,
        title="Water Pump Assembly ISX",
        oem_part_number="4089909",
        manufacturer="Cummins"
    )
    dup_prod2_id = r2.get("id")

    r3 = create_product(
        sku=dup_sku_3,
        title="Oil Pump for CAT C15",
        oem_part_number="681800E",  # Same as product 1's XREF!
        manufacturer="PAI Industries"
    )
    dup_prod3_id = r3.get("id")

    test("created 3 test products for dup detection", all([dup_prod1_id, dup_prod2_id, dup_prod3_id]))

    # ── F7: Duplicate Detection — Exact SKU ─────────────────────────
    section("F7: Exact SKU Match")

    exact_sku = find_similar_products(sku=dup_sku_1)
    test("exact SKU found", len(exact_sku) == 1)
    if exact_sku:
        test("match_reason = exact_sku", exact_sku[0].get("match_reason") == "exact_sku")
        test("confidence = high", exact_sku[0].get("confidence") == "high")

    # ── F8: Duplicate Detection — OEM Part Number ───────────────────
    section("F8: OEM Part Number Match")

    oem_match = find_similar_products(oem_part_number="4089908")
    test("OEM match found", len(oem_match) >= 1)
    test("found product 1 by OEM", any(m.get("id") == dup_prod1_id for m in oem_match))

    # ── F9: Duplicate Detection — XREF Cross-Match ──────────────────
    section("F9: XREF Cross-Match")

    # Product 3 has OEM=681800E, Product 1 has XREF=681800E
    xref_match = find_similar_products(xref_part_number="681800E")
    test("XREF cross-match found", len(xref_match) >= 2,
         f"found {len(xref_match)}")
    test("found product 1 (has 681800E as xref)", any(m.get("id") == dup_prod1_id for m in xref_match))
    test("found product 3 (has 681800E as oem)", any(m.get("id") == dup_prod3_id for m in xref_match))

    # ── F10: Duplicate Detection — Title Similarity ─────────────────
    section("F10: Title Similarity")

    title_match = find_similar_products(title="Water Pump ISX")
    test("title similarity found", len(title_match) >= 1)
    # Should find both Water Pump products
    wp_matches = [m for m in title_match if m.get("id") in (dup_prod1_id, dup_prod2_id)]
    test("found water pump products by title", len(wp_matches) >= 1,
         f"found {len(wp_matches)}")
    if wp_matches:
        test("match_reason = title_similarity", wp_matches[0].get("match_reason") == "title_similarity")

    # Should NOT match Oil Pump (different product type)
    op_in_title = [m for m in title_match if m.get("id") == dup_prod3_id]
    test("oil pump not matched by 'Water Pump' title", len(op_in_title) == 0)

    # ── F11: Duplicate Detection — Exclude ID ───────────────────────
    section("F11: Exclude Self from Matches")

    self_excluded = find_similar_products(sku=dup_sku_1, exclude_id=dup_prod1_id)
    test("self excluded from exact SKU match", len(self_excluded) == 0)

    # ── F12: Duplicate Detection — No Matches ───────────────────────
    section("F12: No Matches")

    no_match = find_similar_products(sku="NONEXISTENT-999999")
    test("no match for nonexistent SKU", len(no_match) == 0)

    no_match2 = find_similar_products(title="ZZ")
    test("no match for short title (< 3 chars)", len(no_match2) == 0)

    # ── F13: Delete SKU Prefix ──────────────────────────────────────
    section("F13: Delete SKU Prefix")

    ok = delete_sku_prefix(prefix2_id)
    test("delete prefix returns True", ok is True)

    remaining = [p for p in get_sku_prefixes() if p.get("id") == prefix2_id]
    test("prefix deleted (not in list)", len(remaining) == 0)

    # ── CLEANUP ─────────────────────────────────────────────────────
    section("Cleanup: Remove Test Data")

    try:
        with get_db() as db:
            # SKU prefixes + sequences
            if prefix_id:
                db.execute("DELETE FROM sku_prefixes WHERE id = ?", (prefix_id,))
            db.execute("DELETE FROM sku_sequences WHERE prefix IN (?, ?)",
                       (test_prefix.upper(), prefix2.upper()))
            # Test products
            db.execute("DELETE FROM products WHERE sku IN (?, ?, ?)",
                       (dup_sku_1, dup_sku_2, dup_sku_3))
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
        print("\n  🎉 ALL PHASE 6 TESTS PASSED — All phases complete!")
    else:
        print(f"\n  ⚠️  {FAIL} test(s) failed — review before closing")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
