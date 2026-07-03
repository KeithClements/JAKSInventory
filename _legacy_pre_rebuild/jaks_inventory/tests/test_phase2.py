"""
JAKS Inventory — Phase 2 Automated Tests
Purchase Orders → Receive → Inventory Update

Tests the full PO lifecycle:
  Create PO → Add Lines → Submit → Receive (partial + full) → Verify Inventory

Run:
    cd c:\\Users\\keith\\Website\\jaks_scraper\\jaks_inventory
    .\\.venv\\Scripts\\Activate.ps1
    $env:DATABASE_URL = "sqlite:///c:/Users/keith/Website/jaks_scraper/output/inventory.db"
    $env:PYTHONPATH = "c:\\Users\\keith\\Website\\jaks_scraper"
    python tests\\test_phase2.py
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
    test_sku_1 = f"PH2-PROD1-{uid}"
    test_sku_2 = f"PH2-PROD2-{uid}"

    # ── SETUP: Create test products and vendor ──────────────────────
    section("SETUP: Create test products and vendor")

    from db.inventory import (
        create_product, get_product_by_sku, get_product_by_id,
        create_purchase_order, get_purchase_order, get_all_purchase_orders,
        add_po_line, update_po_status, receive_po_line,
        adjust_product_quantity, get_inventory_transactions,
        get_all_vendors, create_vendor,
    )
    from db.database import get_db

    # Create two test products
    r1 = create_product(sku=test_sku_1, title="Test Water Pump PH2", cost=100.00, selling_price=250.00)
    test("create product 1", r1.get("success"), str(r1))
    prod1_id = r1.get("id")

    r2 = create_product(sku=test_sku_2, title="Test Oil Pump PH2", cost=80.00, selling_price=200.00)
    test("create product 2", r2.get("success"), str(r2))
    prod2_id = r2.get("id")

    # Get or create a test vendor
    vendors = get_all_vendors()
    if vendors:
        test_vendor_id = vendors[0]["id"]
        test("using existing vendor", True, f"vendor_id={test_vendor_id}")
    else:
        vr = create_vendor(name=f"Test Vendor {uid}")
        test_vendor_id = vr.get("id") if isinstance(vr, dict) else None
        test("created test vendor", test_vendor_id is not None)

    # Verify initial qty is 0
    p1 = get_product_by_sku(test_sku_1)
    p2 = get_product_by_sku(test_sku_2)
    test("product 1 initial qty = 0", (p1.get("qty_on_hand") or 0) == 0)
    test("product 2 initial qty = 0", (p2.get("qty_on_hand") or 0) == 0)

    # ── B1: Create Purchase Order ───────────────────────────────────
    section("B1: Create Purchase Order")

    po_result = create_purchase_order(
        vendor_id=test_vendor_id,
        order_date="2026-03-23",
        expected_date="2026-03-30",
        notes=f"Test PO for Phase 2 ({uid})",
        lines=[
            {"product_id": prod1_id, "quantity_ordered": 10, "unit_cost": 100.00},
            {"product_id": prod2_id, "quantity_ordered": 5, "unit_cost": 80.00},
        ]
    )
    test("create PO returns success", po_result.get("success"), str(po_result))
    test("create PO returns po_id", po_result.get("po_id") is not None, str(po_result))
    test("create PO returns po_number", po_result.get("po_number") is not None, str(po_result))

    po_id = po_result.get("po_id")
    po_number = po_result.get("po_number")

    if po_number:
        test("PO number format PO-YYYY-NNNN", po_number.startswith("PO-2026-"), po_number)

    # ── B2: Verify PO Details ───────────────────────────────────────
    section("B2: Verify PO Details")

    po = get_purchase_order(po_id) if po_id else None
    test("get PO returns data", po is not None)

    if po:
        test("PO status is draft", po.get("status") == "draft", po.get("status"))
        test("PO vendor_id matches", po.get("vendor_id") == test_vendor_id)
        test("PO has vendor_name", po.get("vendor_name") is not None and po.get("vendor_name") != "")
        test("PO order_date matches", po.get("order_date") == "2026-03-23", str(po.get("order_date")))
        test("PO has notes", f"Phase 2 ({uid})" in str(po.get("notes", "")))

        # Lines
        lines = po.get("lines", [])
        test("PO has 2 lines", len(lines) == 2, f"got {len(lines)}")
        if len(lines) >= 2:
            line1 = lines[0]
            line2 = lines[1]
            test("line 1 product_id matches", line1.get("product_id") == prod1_id)
            test("line 1 qty_ordered = 10", line1.get("quantity_ordered") == 10)
            test("line 1 unit_cost = 100", float(line1.get("unit_cost", 0)) == 100.0)
            test("line 1 qty_received = 0", (line1.get("quantity_received") or 0) == 0)
            test("line 2 product_id matches", line2.get("product_id") == prod2_id)
            test("line 2 qty_ordered = 5", line2.get("quantity_ordered") == 5)

        # Totals
        test("PO subtotal = 1400", float(po.get("subtotal", 0)) == 1400.0,
             f"got {po.get('subtotal')}")  # 10*100 + 5*80 = 1400
        test("PO total = 1400", float(po.get("total", 0)) == 1400.0, f"got {po.get('total')}")

    # ── B3: Add Extra Line to PO ────────────────────────────────────
    section("B3: Add Extra Line to PO")

    if po_id:
        add_result = add_po_line(
            po_id=po_id,
            product_id=prod1_id,
            quantity_ordered=5,
            unit_cost=95.00,
            notes="Additional order"
        )
        test("add_po_line returns success", add_result.get("success"), str(add_result))

        # Verify PO now has 3 lines and updated total
        po_updated = get_purchase_order(po_id)
        if po_updated:
            test("PO now has 3 lines", len(po_updated.get("lines", [])) == 3,
                 f"got {len(po_updated.get('lines', []))}")
            # 1400 + 5*95 = 1400 + 475 = 1875
            test("PO subtotal updated to 1875", float(po_updated.get("subtotal", 0)) == 1875.0,
                 f"got {po_updated.get('subtotal')}")

    # ── B4: Submit PO (draft → ordered) ─────────────────────────────
    section("B4: Submit PO (draft → ordered)")

    if po_id:
        submit_result = update_po_status(po_id, "ordered")
        test("submit PO returns success", submit_result.get("success"), str(submit_result))

        po_submitted = get_purchase_order(po_id)
        test("PO status is ordered", po_submitted.get("status") == "ordered",
             po_submitted.get("status"))

    # ── B5: Invalid Status Rejected ─────────────────────────────────
    section("B5: Invalid Status Rejected")

    if po_id:
        bad_result = update_po_status(po_id, "invalid_status")
        test("invalid status rejected", bad_result.get("success") is False, str(bad_result))

    # ── B6: Partial Receive ─────────────────────────────────────────
    section("B6: Partial Receive (line 1: receive 3 of 10)")

    if po_id and po:
        lines = get_purchase_order(po_id).get("lines", [])
        line1_id = lines[0]["id"] if lines else None

        if line1_id:
            rcv_result = receive_po_line(
                po_id=po_id,
                line_id=line1_id,
                quantity_received=3,
                notes="Partial receive test"
            )
            test("receive_po_line returns success", rcv_result.get("success"), str(rcv_result))
            test("qty_received = 3", rcv_result.get("qty_received") == 3, str(rcv_result))
            test("lines_remaining > 0", rcv_result.get("lines_remaining", 0) > 0, str(rcv_result))

            # Verify PO status changed to partial
            po_partial = get_purchase_order(po_id)
            test("PO status is partial", po_partial.get("status") == "partial",
                 po_partial.get("status"))

            # Verify inventory updated
            p1_after = get_product_by_sku(test_sku_1)
            test("product 1 qty_on_hand = 3", (p1_after.get("qty_on_hand") or 0) == 3,
                 f"got {p1_after.get('qty_on_hand')}")

            # Verify inventory transaction created
            txns = get_inventory_transactions(product_id=prod1_id, limit=5)
            test("inventory transaction logged", len(txns) > 0, f"got {len(txns)} txns")
            if txns:
                latest = txns[0]
                test("txn type is receive", latest.get("transaction_type") == "receive",
                     latest.get("transaction_type"))
                test("txn qty_change = 3", latest.get("quantity_change") == 3,
                     str(latest.get("quantity_change")))
                test("txn reference is PO#", po_number in str(latest.get("reference", "")),
                     latest.get("reference"))

    # ── B7: Over-Receive Rejected ───────────────────────────────────
    section("B7: Over-Receive Rejected")

    if po_id and line1_id:
        over_result = receive_po_line(
            po_id=po_id,
            line_id=line1_id,
            quantity_received=99,
            notes="Should fail — over-receive"
        )
        test("over-receive rejected", over_result.get("success") is False, str(over_result))
        test("error mentions remaining", "remaining" in str(over_result.get("error", "")).lower(),
             str(over_result))

    # ── B8: Receive Remaining (complete all lines) ──────────────────
    section("B8: Receive All Remaining Lines")

    if po_id:
        po_current = get_purchase_order(po_id)
        lines = po_current.get("lines", []) if po_current else []

        for line in lines:
            lid = line["id"]
            ordered = line.get("quantity_ordered", 0)
            received = line.get("quantity_received", 0) or 0
            remaining = ordered - received
            if remaining > 0:
                r = receive_po_line(po_id=po_id, line_id=lid, quantity_received=remaining)
                test(f"receive line {lid} ({remaining} units)", r.get("success"), str(r))

        # Verify PO auto-set to received
        po_done = get_purchase_order(po_id)
        test("PO status auto-set to received", po_done.get("status") == "received",
             po_done.get("status"))

        # Verify PO received_date set
        test("PO received_date set", po_done.get("received_date") is not None,
             str(po_done.get("received_date")))

        # Verify final inventory quantities
        # Product 1: line1 (10 units) + line3 (5 units) = 15 total
        p1_final = get_product_by_sku(test_sku_1)
        test("product 1 final qty = 15", (p1_final.get("qty_on_hand") or 0) == 15,
             f"got {p1_final.get('qty_on_hand')}")

        # Product 2: line2 (5 units)
        p2_final = get_product_by_sku(test_sku_2)
        test("product 2 final qty = 5", (p2_final.get("qty_on_hand") or 0) == 5,
             f"got {p2_final.get('qty_on_hand')}")

    # ── B9: Get All Purchase Orders ─────────────────────────────────
    section("B9: List/Filter Purchase Orders")

    all_pos = get_all_purchase_orders()
    test("get_all_purchase_orders returns list", isinstance(all_pos, list))
    test("our PO is in the list", any(p.get("id") == po_id for p in all_pos))

    received_pos = get_all_purchase_orders(status="received")
    test("filter by received status", all(p.get("status") == "received" for p in received_pos))

    vendor_pos = get_all_purchase_orders(vendor_id=test_vendor_id)
    test("filter by vendor", all(p.get("vendor_id") == test_vendor_id for p in vendor_pos))

    # ── B10: Cancel PO Workflow ─────────────────────────────────────
    section("B10: Cancel PO Workflow")

    # Create a new PO just to test cancellation
    cancel_po = create_purchase_order(
        vendor_id=test_vendor_id,
        notes=f"Cancel test ({uid})"
    )
    cancel_id = cancel_po.get("po_id")
    test("create PO for cancel test", cancel_id is not None)

    if cancel_id:
        cancel_result = update_po_status(cancel_id, "cancelled")
        test("cancel PO returns success", cancel_result.get("success"))

        po_cancelled = get_purchase_order(cancel_id)
        test("PO status is cancelled", po_cancelled.get("status") == "cancelled")

    # ── B11: Adjust Product Quantity (manual) ───────────────────────
    section("B11: Manual Inventory Adjustment")

    adj_result = adjust_product_quantity(
        sku=test_sku_1,
        delta=-2,
        txn_type="adjust",
        notes="Testing manual adjustment",
        reference="TEST-ADJ",
        user="test_phase2"
    )
    test("manual adjust returns success", adj_result.get("success"), str(adj_result))
    test("qty_before = 15", adj_result.get("qty_before") == 15, str(adj_result))
    test("qty_after = 13", adj_result.get("qty_after") == 13, str(adj_result))

    # ── B12: Negative Inventory Prevention ──────────────────────────
    section("B12: Negative Inventory Prevention")

    neg_result = adjust_product_quantity(
        sku=test_sku_1,
        delta=-999,
        txn_type="adjust",
        notes="Should fail — negative inventory"
    )
    test("negative inventory blocked", neg_result.get("success") is False, str(neg_result))
    test("error mentions negative/below 0", "below 0" in str(neg_result.get("error", "")).lower() or
         "negative" in str(neg_result.get("error", "")).lower(), str(neg_result))

    # Verify qty unchanged
    p1_check = get_product_by_sku(test_sku_1)
    test("qty unchanged after blocked adjust", (p1_check.get("qty_on_hand") or 0) == 13,
         f"got {p1_check.get('qty_on_hand')}")

    # ── B13: Inventory Transaction History ──────────────────────────
    section("B13: Inventory Transaction History")

    txns = get_inventory_transactions(product_id=prod1_id, limit=20)
    test("transaction history has entries", len(txns) >= 3, f"got {len(txns)}")

    # Should have: receive (3), receive (7), receive (5), adjust (-2) = 4 txns for prod1
    receive_txns = [t for t in txns if t.get("transaction_type") == "receive"]
    adjust_txns = [t for t in txns if t.get("transaction_type") == "adjust"]
    test("has receive transactions", len(receive_txns) >= 2, f"got {len(receive_txns)}")
    test("has adjust transaction", len(adjust_txns) >= 1, f"got {len(adjust_txns)}")

    # ── CLEANUP ─────────────────────────────────────────────────────
    section("Cleanup: Remove Test Data")

    try:
        with get_db() as db:
            # Delete in dependency order
            if po_id:
                db.execute("DELETE FROM purchase_order_lines WHERE po_id = ?", (po_id,))
                db.execute("DELETE FROM purchase_orders WHERE id = ?", (po_id,))
            if cancel_id:
                db.execute("DELETE FROM purchase_order_lines WHERE po_id = ?", (cancel_id,))
                db.execute("DELETE FROM purchase_orders WHERE id = ?", (cancel_id,))
            if prod1_id:
                db.execute("DELETE FROM inventory_transactions WHERE product_id = ?", (prod1_id,))
            if prod2_id:
                db.execute("DELETE FROM inventory_transactions WHERE product_id = ?", (prod2_id,))
            db.execute("DELETE FROM products WHERE sku IN (?, ?)", (test_sku_1, test_sku_2))
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
        print("\n  🎉 ALL PHASE 2 TESTS PASSED — Ready for Phase 3!")
    else:
        print(f"\n  ⚠️  {FAIL} test(s) failed — fix before proceeding to Phase 3")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
