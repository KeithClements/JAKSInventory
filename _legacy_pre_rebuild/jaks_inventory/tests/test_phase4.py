"""
JAKS Inventory — Phase 4 Automated Tests
Core Returns Lifecycle

Tests the full core return lifecycle:
  Invoice finalize → Core auto-created → Receive core → Issue credit → Void
  Also: listing, filtering, summary, customer/invoice lookups

Run:
    cd c:\\Users\\keith\\Website\\jaks_scraper\\jaks_inventory
    .\\.venv\\Scripts\\Activate.ps1
    $env:DATABASE_URL = "sqlite:///c:/Users/keith/Website/jaks_scraper/output/inventory.db"
    $env:PYTHONPATH = "c:\\Users\\keith\\Website\\jaks_scraper"
    python tests\\test_phase4.py
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
    test_sku = f"PH4-CORE-{uid}"

    from db.inventory import (
        create_product, get_product_by_sku, adjust_product_quantity,
        create_customer,
        create_invoice, add_invoice_line, finalize_invoice,
        create_core_return, create_core_from_invoice_line,
        get_core_return, list_core_returns,
        receive_core, issue_core_credit, void_core_return,
        delete_core_return,
        get_pending_cores, get_core_returns_for_invoice,
        get_core_returns_for_customer, get_core_summary,
        update_core_return,
    )
    from db.database import get_db

    # ── SETUP ───────────────────────────────────────────────────────
    section("SETUP: Product, Customer, Invoice with Core Charge")

    # Product with core charge
    r = create_product(
        sku=test_sku,
        title="Test Turbo Actuator PH4",
        cost=200.00,
        selling_price=500.00,
        core_charge=300.00,
        manufacturer="PAI Industries",
    )
    test("create product with core_charge=300", r.get("success"))
    prod_id = r.get("id")

    # Stock it up
    adj = adjust_product_quantity(sku=test_sku, delta=10, txn_type="receive", reference="SETUP-PH4")
    test("stock product (10 units)", adj.get("success"))

    # Customer
    cust_id = create_customer(
        name=f"Core Customer {uid}",
        company="Core Fleet LLC",
        phone="702-555-0200"
    )
    test("create customer", cust_id and cust_id > 0)

    # Create and finalize an invoice to auto-generate cores
    inv = create_invoice(customer_id=cust_id, user="test_phase4")
    inv_id = inv.get("id")
    test("create invoice", inv_id is not None)

    line_id = add_invoice_line(
        invoice_id=inv_id,
        product_id=prod_id,
        quantity=3,
        unit_price=500.00,
    )
    test("add invoice line (qty=3)", line_id is not None and line_id > 0)

    fin = finalize_invoice(inv_id, deduct_inventory=True, user="test_phase4")
    test("finalize invoice", fin.get("success"))
    auto_cores = fin.get("cores_created", [])
    test("auto-created 3 cores", len(auto_cores) == 3, f"got {len(auto_cores)}")

    # Save core IDs for later tests
    core_ids = [c.get("core_id") for c in auto_cores if c.get("core_id")]
    test("cores have IDs", len(core_ids) == 3, str(core_ids))

    # ── D1: Verify Auto-Created Core Details ────────────────────────
    section("D1: Verify Auto-Created Core Details")

    if core_ids:
        core = get_core_return(core_ids[0])
        test("get_core_return returns data", core is not None)
        if core:
            test("status = pending", core.get("status") == "pending")
            test("product_id matches", core.get("product_id") == prod_id)
            test("customer_id matches", core.get("customer_id") == cust_id)
            test("core_charge = 300", float(core.get("core_charge") or 0) == 300.0)
            test("invoice_id matches", core.get("invoice_id") == inv_id)
            test("due_date is set", core.get("due_date") is not None)
            test("credit_issued = 0", float(core.get("credit_issued") or 0) == 0.0)
            test("has sku", core.get("sku") == test_sku, core.get("sku"))
            test("has product_title", core.get("product_title") is not None)

    # ── D2: List Core Returns ───────────────────────────────────────
    section("D2: List Core Returns")

    all_cores = list_core_returns()
    test("list_core_returns returns list", isinstance(all_cores, list))
    test("list has entries", len(all_cores) > 0)

    pending_cores = list_core_returns(status="pending")
    test("filter by pending", all(c.get("status") == "pending" for c in pending_cores))
    test("our cores in pending list",
         sum(1 for c in pending_cores if c.get("id") in core_ids) == 3,
         f"found {sum(1 for c in pending_cores if c.get('id') in core_ids)}")

    customer_filtered = list_core_returns(customer_id=cust_id)
    test("filter by customer", all(c.get("customer_id") == cust_id for c in customer_filtered))

    product_filtered = list_core_returns(product_id=prod_id)
    test("filter by product", all(c.get("product_id") == prod_id for c in product_filtered))

    # ── D3: Get Pending Cores (with overdue calc) ───────────────────
    section("D3: Get Pending Cores (SQLite-compatible)")

    pending = get_pending_cores()
    test("get_pending_cores returns list", isinstance(pending, list))
    test("pending list has our cores",
         sum(1 for p in pending if p.get("id") in core_ids) >= 1)

    # Filter by customer
    cust_pending = get_pending_cores(customer_id=cust_id)
    test("pending filter by customer", len(cust_pending) >= 3)

    # Overdue filter (cores due in 30 days, so days_overdue=0 should NOT filter them out)
    overdue = get_pending_cores(days_overdue=0)
    test("days_overdue=0 returns list", isinstance(overdue, list))

    # ── D4: Get Core Returns for Invoice ────────────────────────────
    section("D4: Core Returns by Invoice")

    inv_cores = get_core_returns_for_invoice(inv_id)
    test("get_core_returns_for_invoice", len(inv_cores) == 3, f"got {len(inv_cores)}")
    if inv_cores:
        test("all linked to our invoice", all(c.get("invoice_id") == inv_id for c in inv_cores))
        test("all have sku", all(c.get("sku") == test_sku for c in inv_cores))

    # ── D5: Get Core Returns for Customer ───────────────────────────
    section("D5: Core Returns by Customer")

    cust_cores = get_core_returns_for_customer(cust_id)
    test("get_core_returns_for_customer", len(cust_cores) >= 3)

    cust_pending_cores = get_core_returns_for_customer(cust_id, status="pending")
    test("customer pending filter", len(cust_pending_cores) >= 3)
    test("all pending", all(c.get("status") == "pending" for c in cust_pending_cores))

    # ── D6: Receive Core (pending → received) ──────────────────────
    section("D6: Receive Core (pending → received)")

    if len(core_ids) >= 1:
        core_to_receive = core_ids[0]
        ok = receive_core(core_to_receive, condition="good", serial_number="CORE-SN-001", notes="Good condition")
        test("receive_core returns True", ok is True)

        received = get_core_return(core_to_receive)
        test("status = received", received.get("status") == "received")
        test("condition = good", received.get("condition") == "good")
        test("serial_number set", received.get("serial_number") == "CORE-SN-001")
        test("received_date = today", received.get("received_date") == str(date.today()))
        test("notes set", "Good condition" in (received.get("notes") or ""))

        # Verify inventory NOT adjusted (core receipt doesn't add stock)
        prod_after = get_product_by_sku(test_sku)
        test("qty_on_hand unchanged (7)", (prod_after.get("qty_on_hand") or 0) == 7,
             f"got {prod_after.get('qty_on_hand')}")

    # ── D7: Issue Core Credit (received → credited) ────────────────
    section("D7: Issue Core Credit (received → credited)")

    if len(core_ids) >= 1:
        ok = issue_core_credit(core_to_receive, credit_amount=300.00, notes="Full credit")
        test("issue_core_credit returns True", ok is True)

        credited = get_core_return(core_to_receive)
        test("status = credited", credited.get("status") == "credited")
        test("credit_issued = 300", float(credited.get("credit_issued") or 0) == 300.0)
        test("credit_date = today", credited.get("credit_date") == str(date.today()))

    # ── D8: Receive + Partial Credit ────────────────────────────────
    section("D8: Receive + Partial Credit")

    if len(core_ids) >= 2:
        core2 = core_ids[1]
        receive_core(core2, condition="damaged", notes="Cracked housing")
        damaged = get_core_return(core2)
        test("core 2 received (damaged)", damaged.get("status") == "received")
        test("condition = damaged", damaged.get("condition") == "damaged")

        # 50% credit for damaged core
        issue_core_credit(core2, credit_amount=150.00, notes="50% - damaged")
        partial = get_core_return(core2)
        test("core 2 credited at 50%", float(partial.get("credit_issued") or 0) == 150.0)
        test("core 2 status = credited", partial.get("status") == "credited")

    # ── D9: Void Core Return ────────────────────────────────────────
    section("D9: Void Core Return (pending → voided)")

    if len(core_ids) >= 3:
        core3 = core_ids[2]
        ok = void_core_return(core3, notes="Customer not returning")
        test("void_core_return returns True", ok is True)

        voided = get_core_return(core3)
        test("status = voided", voided.get("status") == "voided")

    # ── D10: Core Summary Dashboard ─────────────────────────────────
    section("D10: Core Summary Dashboard")

    summary = get_core_summary()
    test("get_core_summary returns dict", isinstance(summary, dict))
    test("has pending key", "pending" in summary)
    test("has received key", "received" in summary)
    test("has credited key", "credited" in summary)
    test("has voided key", "voided" in summary)

    # Credited should show totals
    if "credited" in summary:
        test("credited has count", summary["credited"].get("count", 0) > 0)
        test("credited has charge", summary["credited"].get("charge", 0) > 0)
        test("credited shows credit amount", summary["credited"].get("credited", 0) > 0)

    # ── D11: Manual Core Return Creation ────────────────────────────
    section("D11: Manual Core Return (not from invoice)")

    manual_core_id = create_core_return(
        product_id=prod_id,
        customer_id=cust_id,
        core_charge=300.00,
        due_date=str(date.today() + timedelta(days=14)),
        notes="Manual entry - customer walked in"
    )
    test("create_core_return returns id", manual_core_id is not None and manual_core_id > 0)

    manual_core = get_core_return(manual_core_id)
    test("manual core status = pending", manual_core.get("status") == "pending")
    test("manual core has no invoice_id", manual_core.get("invoice_id") is None)
    test("manual core charge = 300", float(manual_core.get("core_charge") or 0) == 300.0)

    # ── D12: Update Core Return (generic) ───────────────────────────
    section("D12: Update Core Return (generic)")

    if manual_core_id:
        ok = update_core_return(manual_core_id, notes="Updated notes via generic update")
        test("update_core_return returns True", ok is True)

        updated = get_core_return(manual_core_id)
        test("notes updated", "Updated notes" in (updated.get("notes") or ""))

    # ── D13: Delete Core Return ─────────────────────────────────────
    section("D13: Delete Core Return")

    if manual_core_id:
        ok = delete_core_return(manual_core_id)
        test("delete_core_return returns True", ok is True)

        gone = get_core_return(manual_core_id)
        test("core deleted (not found)", gone is None)

    # ── D14: create_core_from_invoice_line Directly ─────────────────
    section("D14: create_core_from_invoice_line (direct call)")

    direct_core_id = create_core_from_invoice_line(
        invoice_id=inv_id,
        invoice_line_id=line_id,
        product_id=prod_id,
        customer_id=cust_id,
        core_charge=300.00,
        due_days=45,
    )
    test("returns core id", direct_core_id is not None and direct_core_id > 0)

    dc = get_core_return(direct_core_id)
    test("status = pending", dc.get("status") == "pending")
    test("due_date = today+45", dc.get("due_date") == str(date.today() + timedelta(days=45)))
    test("invoice_line_id set", dc.get("invoice_line_id") == line_id)

    # ── CLEANUP ─────────────────────────────────────────────────────
    section("Cleanup: Remove Test Data")

    try:
        with get_db() as db:
            # Core returns (all for this invoice + the direct one)
            db.execute("DELETE FROM core_returns WHERE invoice_id = ?", (inv_id,))
            if direct_core_id:
                db.execute("DELETE FROM core_returns WHERE id = ?", (direct_core_id,))
            # Invoice lines + invoice
            db.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (inv_id,))
            db.execute("DELETE FROM invoices WHERE id = ?", (inv_id,))
            # Inventory transactions
            if prod_id:
                db.execute("DELETE FROM inventory_transactions WHERE product_id = ?", (prod_id,))
            # Product
            db.execute("DELETE FROM products WHERE sku = ?", (test_sku,))
            # Customer
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
        print("\n  🎉 ALL PHASE 4 TESTS PASSED — Ready for Phase 5!")
    else:
        print(f"\n  ⚠️  {FAIL} test(s) failed — fix before proceeding to Phase 5")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
