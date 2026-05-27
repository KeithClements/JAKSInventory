"""
JAKS Inventory — Phase 3 Automated Tests
Sales Orders → Invoices → Payment → Inventory Deduction

Tests the full sales lifecycle under the current explicit-pick workflow:
    Stock up → Create SO → Add Lines → Allocate/Reserve → Convert to Invoice → Finalize → Record Payment

Run:
    cd c:\\Users\\keith\\Website\\jaks_scraper\\jaks_inventory
    .\\.venv\\Scripts\\Activate.ps1
    $env:DATABASE_URL = "sqlite:///c:/Users/keith/Website/jaks_scraper/output/inventory.db"
    $env:PYTHONPATH = "c:\\Users\\keith\\Website\\jaks_scraper"
    python tests\\test_phase3.py
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
    test_sku_1 = f"PH3-WP-{uid}"
    test_sku_2 = f"PH3-OP-{uid}"

    from db.inventory import (
        create_product, get_product_by_sku, get_product_by_id,
        adjust_product_quantity,
        create_customer, get_customer,
        create_sales_order, get_sales_order, list_sales_orders,
        update_sales_order, add_so_line, get_so_lines, remove_so_line,
        convert_so_to_invoice, get_so_summary,
        create_invoice, get_invoice, get_all_invoices,
        add_invoice_line, remove_invoice_line, update_invoice_status,
        finalize_invoice, record_payment, get_customer_invoices,
        get_inventory_transactions,
    )
    from db.database import get_db

    # ── SETUP ───────────────────────────────────────────────────────
    section("SETUP: Products, Customer, and Stock")

    # Create products (with core charge on product 1 to test core creation)
    r1 = create_product(
        sku=test_sku_1,
        title="Test Water Pump PH3",
        cost=100.00,
        selling_price=250.00,
        core_charge=150.00,
        manufacturer="PAI Industries",
    )
    test("create product 1 (with core charge)", r1.get("success"), str(r1))
    prod1_id = r1.get("id")

    r2 = create_product(
        sku=test_sku_2,
        title="Test Oil Pump PH3",
        cost=80.00,
        selling_price=200.00,
    )
    test("create product 2 (no core)", r2.get("success"), str(r2))
    prod2_id = r2.get("id")

    # Stock up products so we have inventory to sell
    adj1 = adjust_product_quantity(sku=test_sku_1, delta=20, txn_type="receive", reference="SETUP-PH3")
    adj2 = adjust_product_quantity(sku=test_sku_2, delta=15, txn_type="receive", reference="SETUP-PH3")
    test("product 1 stocked (20)", adj1.get("success"))
    test("product 2 stocked (15)", adj2.get("success"))

    # Create test customer
    cust_id = create_customer(
        name=f"Test Customer {uid}",
        company="Test Fleet Services",
        email="test@fleet.com",
        phone="702-555-0199",
        payment_terms="Net 30"
    )
    test("create customer", cust_id is not None and cust_id > 0, str(cust_id))

    cust = get_customer(cust_id) if cust_id else None
    test("customer has correct name", cust and f"Test Customer {uid}" in cust.get("name", ""))

    # ── C1: Create Sales Order ──────────────────────────────────────
    section("C1: Create Sales Order")

    so_result = create_sales_order(
        customer_id=cust_id,
        priority="urgent",
        promised_date="2026-03-25",
        ship_method="Local Delivery",
        notes="Phase 3 test order",
        internal_notes="Internal: automated test",
        user="test_phase3"
    )
    test("create SO returns dict", isinstance(so_result, dict))
    so_id = so_result.get("id")
    so_number = so_result.get("so_number")
    test("SO has id", so_id is not None, str(so_result))
    test("SO has number", so_number is not None, str(so_result))
    if so_number:
        test("SO number format SO-YYYY-NNNN", so_number.startswith("SO-2026-"), so_number)

    # ── C2: Verify SO Details ───────────────────────────────────────
    section("C2: Verify SO Details")

    so = get_sales_order(so_id) if so_id else None
    test("get SO returns data", so is not None)
    if so:
        test("SO status is draft", so.get("status") == "draft")
        test("SO customer_id matches", so.get("customer_id") == cust_id)
        test("SO priority is urgent", so.get("priority") == "urgent")
        test("SO has customer_name", so.get("customer_name") is not None)
        test("SO has customer_company", so.get("customer_company") == "Test Fleet Services",
             so.get("customer_company"))

    # ── C3: Add SO Lines (with inventory reservation) ───────────────
    section("C3: Add SO Lines + Inventory Reservation")

    line1_id = None
    line2_id = None

    if so_id and prod1_id:
        line1_id = add_so_line(
            so_id=so_id,
            product_id=prod1_id,
            quantity=3,
            unit_price=250.00,
            notes="Water pump order"
        )
        test("add SO line 1 returns id", line1_id is not None and line1_id > 0, str(line1_id))

    if so_id and prod2_id:
        line2_id = add_so_line(
            so_id=so_id,
            product_id=prod2_id,
            quantity=2,
            unit_price=200.00,
        )
        test("add SO line 2 returns id", line2_id is not None and line2_id > 0, str(line2_id))

    # Verify inventory reserved
    p1_after_so = get_product_by_sku(test_sku_1)
    p2_after_so = get_product_by_sku(test_sku_2)
    test("product 1 qty_reserved = 3", (p1_after_so.get("qty_reserved") or 0) == 3,
         f"got {p1_after_so.get('qty_reserved')}")
    test("product 2 qty_reserved = 2", (p2_after_so.get("qty_reserved") or 0) == 2,
         f"got {p2_after_so.get('qty_reserved')}")
    # qty_on_hand should still be the same (reservation doesn't deduct)
    test("product 1 qty_on_hand still 20", (p1_after_so.get("qty_on_hand") or 0) == 20)
    test("product 2 qty_on_hand still 15", (p2_after_so.get("qty_on_hand") or 0) == 15)

    # Verify SO lines: current workflow allocates immediately but does not auto-pick.
    so_lines = get_so_lines(so_id) if so_id else []
    test("SO has 2 lines", len(so_lines) == 2, f"got {len(so_lines)}")
    if so_lines:
        test("line 1 allocated_qty = 3", (so_lines[0].get("allocated_qty") or 0) == 3,
             so_lines[0].get("allocated_qty"))
        test("line 1 qty_picked = 0 before explicit pick", (so_lines[0].get("quantity_picked") or 0) == 0,
             so_lines[0].get("quantity_picked"))
        test("line 1 qty_backordered = 0", (so_lines[0].get("quantity_backordered") or 0) == 0)

    # Verify SO totals recalculated
    so_updated = get_sales_order(so_id) if so_id else None
    if so_updated:
        expected_subtotal = 3 * 250 + 2 * 200  # 750 + 400 = 1150
        test("SO subtotal = 1150", float(so_updated.get("subtotal", 0)) == 1150.0,
             f"got {so_updated.get('subtotal')}")

    # ── C4: Remove SO Line (releases reservation) ──────────────────
    section("C4: Remove SO Line + Release Reservation")

    if line2_id:
        removed = remove_so_line(line2_id)
        test("remove SO line 2", removed is True)

        # Verify reservation released
        p2_after_remove = get_product_by_sku(test_sku_2)
        test("product 2 qty_reserved back to 0", (p2_after_remove.get("qty_reserved") or 0) == 0,
             f"got {p2_after_remove.get('qty_reserved')}")

        # Re-add it for the rest of the test
        line2_id = add_so_line(so_id=so_id, product_id=prod2_id, quantity=2, unit_price=200.00)
        test("re-add SO line 2", line2_id is not None and line2_id > 0)

    # ── C5: List/Filter Sales Orders ────────────────────────────────
    section("C5: List/Filter Sales Orders")

    all_sos = list_sales_orders()
    test("list_sales_orders returns list", isinstance(all_sos, list))
    test("our SO is in the list", any(s.get("id") == so_id for s in all_sos))

    draft_sos = list_sales_orders(status="draft")
    test("filter by draft status", all(s.get("status") == "draft" for s in draft_sos))

    # ── C6: Convert SO to Invoice ───────────────────────────────────
    section("C6: Convert SO → Invoice")

    invoice_result = None
    invoice_id = None
    inv_number = None

    if so_id:
        invoice_result = convert_so_to_invoice(so_id, user="test_phase3")
        test("convert returns dict", isinstance(invoice_result, dict))
        invoice_id = invoice_result.get("invoice_id")
        inv_number = invoice_result.get("invoice_number")
        test("conversion returns invoice_id", invoice_id is not None, str(invoice_result))
        test("conversion returns invoice_number", inv_number is not None, str(invoice_result))

        # Verify SO marked as invoiced
        so_invoiced = get_sales_order(so_id)
        test("SO status = invoiced", so_invoiced.get("status") == "invoiced",
             so_invoiced.get("status"))
        test("SO invoice_id set", so_invoiced.get("invoice_id") == invoice_id)
        test("SO completed_at set", so_invoiced.get("completed_at") is not None)

        # Reservations should be released
        p1_after_conv = get_product_by_sku(test_sku_1)
        p2_after_conv = get_product_by_sku(test_sku_2)
        test("product 1 qty_reserved released to 0",
             (p1_after_conv.get("qty_reserved") or 0) == 0,
             f"got {p1_after_conv.get('qty_reserved')}")
        test("product 2 qty_reserved released to 0",
             (p2_after_conv.get("qty_reserved") or 0) == 0,
             f"got {p2_after_conv.get('qty_reserved')}")

        # But qty_on_hand still unchanged (not deducted until finalize)
        test("product 1 qty_on_hand still 20", (p1_after_conv.get("qty_on_hand") or 0) == 20)

        # Cannot convert again
        dup = convert_so_to_invoice(so_id)
        test("re-convert rejected", "error" in dup, str(dup))

    # ── C7: Verify Invoice Details ──────────────────────────────────
    section("C7: Verify Invoice Details")

    inv = get_invoice(invoice_id) if invoice_id else None
    test("get invoice returns data", inv is not None)
    if inv:
        test("invoice status is draft", inv.get("status") == "draft")
        test("invoice customer_id matches", inv.get("customer_id") == cust_id)
        test("invoice has customer_name", inv.get("customer_name") is not None)
        test("invoice total = SO total", float(inv.get("total", 0)) == float(so_updated.get("total", 0)),
             f"inv={inv.get('total')}, so={so_updated.get('total')}")
        test("invoice balance_due = total", float(inv.get("balance_due", 0)) == float(inv.get("total", 0)))

        inv_lines = inv.get("lines", [])
        test("invoice has 2 lines", len(inv_lines) == 2, f"got {len(inv_lines)}")
        if inv_lines:
            test("inv line 1 sku matches", inv_lines[0].get("sku") == test_sku_1,
                 inv_lines[0].get("sku"))
            test("inv line 1 qty = 3", int(inv_lines[0].get("quantity", 0)) == 3)

    # ── C8: Direct Invoice Creation (no SO) ─────────────────────────
    section("C8: Direct Invoice Creation (no SO)")

    direct_inv = create_invoice(
        customer_id=cust_id,
        notes="Direct invoice test",
        user="test_phase3"
    )
    direct_inv_id = direct_inv.get("id")
    direct_inv_num = direct_inv.get("invoice_number")
    test("create direct invoice", direct_inv_id is not None, str(direct_inv))
    test("direct invoice has number", direct_inv_num is not None)

    if direct_inv_id and prod1_id:
        line_id = add_invoice_line(
            invoice_id=direct_inv_id,
            product_id=prod1_id,
            quantity=1,
            unit_price=250.00,
            description="Direct Water Pump"
        )
        test("add invoice line returns id", line_id is not None and line_id > 0)

        # Verify totals recalculated
        direct_inv_data = get_invoice(direct_inv_id)
        test("direct invoice subtotal = 250", float(direct_inv_data.get("subtotal", 0)) == 250.0,
             f"got {direct_inv_data.get('subtotal')}")

    # ── C9: Finalize Invoice (deduct inventory + core creation) ─────
    section("C9: Finalize Invoice (SO-based)")

    if invoice_id:
        fin_result = finalize_invoice(invoice_id, deduct_inventory=True, user="test_phase3")
        test("finalize returns success", fin_result.get("success"), str(fin_result))

        # Check inventory deducted
        deducted = fin_result.get("deducted", [])
        test("deducted has entries", len(deducted) > 0, str(deducted))

        p1_final = get_product_by_sku(test_sku_1)
        p2_final = get_product_by_sku(test_sku_2)
        # Product 1: was 20, sold 3 → should be 17
        test("product 1 qty = 17 (20-3)", (p1_final.get("qty_on_hand") or 0) == 17,
             f"got {p1_final.get('qty_on_hand')}")
        # Product 2: was 15, sold 2 → should be 13
        test("product 2 qty = 13 (15-2)", (p2_final.get("qty_on_hand") or 0) == 13,
             f"got {p2_final.get('qty_on_hand')}")

        # Invoice status should be "sent" after finalize
        inv_after = get_invoice(invoice_id)
        test("invoice status = sent", inv_after.get("status") == "sent",
             inv_after.get("status"))

        # Core returns should be created for product 1 (which has core_charge=150)
        cores = fin_result.get("cores_created", [])
        test("core returns created", len(cores) > 0, f"got {len(cores)} cores")
        if cores:
            test("cores for product 1", any(c.get("product_id") == prod1_id for c in cores))
            test("core charge = 150", any(c.get("core_charge") == 150.0 for c in cores))
            # Should be 3 cores (1 per unit sold)
            prod1_cores = [c for c in cores if c.get("product_id") == prod1_id]
            test("3 cores created (1 per unit)", len(prod1_cores) == 3,
                 f"got {len(prod1_cores)}")

        # Cannot finalize again
        re_fin = finalize_invoice(invoice_id)
        test("re-finalize rejected", re_fin.get("success") is False, str(re_fin))

        # Inventory transactions logged
        txns = get_inventory_transactions(product_id=prod1_id, limit=10)
        sale_txns = [t for t in txns if t.get("transaction_type") == "sale"]
        test("sale transactions logged", len(sale_txns) > 0, f"got {len(sale_txns)}")

    # ── C10: Record Payment ─────────────────────────────────────────
    section("C10: Record Payment")

    if invoice_id:
        inv_before = get_invoice(invoice_id)
        total = float(inv_before.get("total", 0))

        # Partial payment
        pay1 = record_payment(invoice_id, amount=500.00, payment_method="check", notes="Partial payment")
        test("partial payment recorded", pay1 is True)

        inv_partial = get_invoice(invoice_id)
        test("amount_paid = 500", float(inv_partial.get("amount_paid", 0)) == 500.0,
             f"got {inv_partial.get('amount_paid')}")
        test("status = partial", inv_partial.get("status") == "partial",
             inv_partial.get("status"))
        expected_balance = total - 500.0
        test("balance_due correct", abs(float(inv_partial.get("balance_due", 0)) - expected_balance) < 0.01,
             f"expected {expected_balance}, got {inv_partial.get('balance_due')}")

        # Pay remaining
        pay2 = record_payment(invoice_id, amount=expected_balance, payment_method="credit_card")
        test("full payment recorded", pay2 is True)

        inv_paid = get_invoice(invoice_id)
        test("status = paid", inv_paid.get("status") == "paid", inv_paid.get("status"))
        test("balance_due = 0", float(inv_paid.get("balance_due", 0)) == 0.0,
             f"got {inv_paid.get('balance_due')}")

    # ── C11: Void Invoice ───────────────────────────────────────────
    section("C11: Void Invoice")

    if direct_inv_id:
        void_ok = update_invoice_status(direct_inv_id, "void")
        test("void invoice", void_ok is True)

        inv_voided = get_invoice(direct_inv_id)
        test("status = void", inv_voided.get("status") == "void")

    # ── C12: Customer Invoices ──────────────────────────────────────
    section("C12: Customer Invoices")

    if cust_id:
        cust_invs = get_customer_invoices(cust_id)
        test("customer has invoices", len(cust_invs) >= 2, f"got {len(cust_invs)}")

    # ── C13: Get All Invoices ───────────────────────────────────────
    section("C13: List/Filter Invoices")

    all_invs = get_all_invoices()
    test("get_all_invoices returns list", isinstance(all_invs, list))
    test("has invoices", len(all_invs) > 0)

    paid_invs = get_all_invoices(status="paid")
    test("filter by paid", all(i.get("status") == "paid" for i in paid_invs))

    # ── C14: SO Summary ────────────────────────────────────────────
    section("C14: SO Summary Dashboard")

    summary = get_so_summary()
    test("SO summary returns dict", isinstance(summary, dict))
    test("summary has invoiced count", "invoiced" in summary)

    # ── CLEANUP ─────────────────────────────────────────────────────
    section("Cleanup: Remove Test Data")

    try:
        with get_db() as db:
            invoice_ids = [iid for iid in (invoice_id, direct_inv_id) if iid]

            # Delete in FK-safe order: children before parents
            # 1) Invoice-linked children that do not cascade on invoice delete.
            for iid in invoice_ids:
                db.execute("DELETE FROM core_returns WHERE invoice_id = ? OR credit_invoice_id = ?", (iid, iid))
                db.execute("UPDATE serial_numbers SET invoice_id = NULL, sold_date = NULL, status = 'available' WHERE invoice_id = ?", (iid,))
                db.execute("DELETE FROM work_orders WHERE invoice_id = ?", (iid,))
                db.execute("DELETE FROM returns WHERE invoice_id = ?", (iid,))

            # 2) SO lines + SOs (SO has FK → invoices, so delete before invoices)
            if so_id:
                db.execute("DELETE FROM shipments WHERE so_id = ?", (so_id,))
                db.execute("DELETE FROM so_activity_log WHERE so_id = ?", (so_id,))
                db.execute("DELETE FROM sales_order_lines WHERE so_id = ?", (so_id,))
                db.execute("DELETE FROM sales_orders WHERE id = ?", (so_id,))
            # 3) Invoice lines + invoices
            for iid in invoice_ids:
                db.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (iid,))
                db.execute("DELETE FROM invoices WHERE id = ?", (iid,))
            # 4) Inventory transactions
            if prod1_id:
                db.execute("DELETE FROM inventory_transactions WHERE product_id = ?", (prod1_id,))
            if prod2_id:
                db.execute("DELETE FROM inventory_transactions WHERE product_id = ?", (prod2_id,))
            # 5) Products
            db.execute("DELETE FROM products WHERE sku IN (?, ?)", (test_sku_1, test_sku_2))
            # 6) Customer
            if cust_id:
                db.execute("DELETE FROM customers WHERE id = ?", (cust_id,))
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
        print("\n  🎉 ALL PHASE 3 TESTS PASSED — Ready for Phase 4!")
    else:
        print(f"\n  ⚠️  {FAIL} test(s) failed — fix before proceeding to Phase 4")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
