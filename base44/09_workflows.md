# Workflows (User Click-by-Click)

Concrete step-by-step flows. Each is the sequence of UI actions a user takes
end-to-end. Use these to validate that the rebuilt screens hang together.

---

## W1. Take an order from inbound call

1. Phone rings. Rep picks up. Customer says "I need a Detroit S60 turbo."
2. Rep presses **Ctrl+K** anywhere → quick-nav → types `quote new` → Enter.
3. **New Quote** dialog opens.
4. **Customer field**: rep types customer name, picks from autocomplete.
   If not found, presses **+ Add Customer**, fills minimal info, saves.
5. **ESN field**: rep asks customer for engine serial. Types it in.
   App searches ESN history → "This ESN has 4 prior parts. Show?" → rep clicks
   to review (decision aid).
6. **+ Add part**: rep types "S60 turbo HX55" → Part Finder shows local matches,
   PAI matches, HHP matches.
7. Rep picks the local SKU with qty > 0. Line added with default tier price.
8. **Suggested sells** chip appears: "Customers who buy this also buy: gaskets,
   oil cooler". Rep can add or dismiss.
9. **Core charge** child line auto-added (turbo has has_core=1, $250 core).
10. **Warranty offered**: rep picks "JAK 18mo" from the warranty dropdown
    (adds a $30 warranty fee line).
11. **Save Draft** or **Send Quote**. Sending → generates PDF, emails customer,
    sends SMS "Your quote Q-2026-00045 is ready", sets follow_up_at = now+3d.
12. Quote appears in Sales → Quotes screen with status `Sent`.

## W2. Convert quote to invoice (in-stock)

1. Open the quote.
2. **Convert to SO** button. Dialog asks to confirm lines.
3. SO created with status `Confirmed`. Inventory reserved for in-stock lines.
4. Warehouse picks via **Pick Ticket** PDF. Each line marked picked in the
   SO detail.
5. **Pack & Ship**: enter carrier + tracking + ship date. Lines flip to
   `shipped`. Qty_on_hand decremented (inventory_audit row created).
6. **Convert to Invoice** button. Invoice created with all shipped lines.
7. **Take Payment**: method=Card, amount=full, save. Payment row created,
   AR balance → 0, QBO push queued.
8. Customer auto-receives SMS: "Invoice INV-2026-00831 paid. Thanks!"

## W3. Backordered line → PO drop-ship

1. Quote line for a part with qty_on_hand=0.
2. Convert to SO; line is `Backordered`.
3. Sales (or purchasing) clicks **Create PO for backorder** on the line.
4. PO Draft pre-populated with the SO line, vendor = preferred_vendor.
   `purchase_orders.linked_so_id` set.
5. Purchasing reviews and **Sends PO**. Vendor email/fax sent.
6. When vendor ships drop-ship directly to customer:
   - Purchasing opens PO → marks line as `drop_shipped`, enters tracking.
   - SO line auto-flips to `shipped` (qty_on_hand untouched because product
     never sat in our warehouse).
7. SO can be invoiced as usual.

## W4. Customer returns a core

1. Customer arrives with a used core.
2. Rep navigates to **Core Processing → Customer Cores**.
3. Filter by customer. Sees 5 outstanding cores.
4. Selects the matching row (Turbo, INV-2026-00831, $250, 31 days old).
5. Clicks **Accept Return**.
6. Dialog: inspect condition. Picks `Acceptable`.
7. Picks credit method: `Store credit`. Saves.
8. → `customer_credits` row created for $250.
9. → `customer_cores.status = 'returned'`.
10. → `inventory_audit` row: core part qty +1 (now we have it as a vendor-core obligation).
11. → `vendor_core_obligations` row created against the original vendor.
12. Customer 360 → Credits tab shows new $250 credit applicable to next invoice.

## W5. Send used core back to vendor (RGA)

1. **Core Processing → Vendor Returns → Vendor Cores Board**.
2. Pick vendor. See list of obligations.
3. Multi-select rows (say 8 cores). Click **Create RGA**.
4. RGA Draft dialog opens with the lines pre-populated.
5. Submit to vendor (email or web portal). Status → `Submitted`.
6. Vendor returns an RGA number; rep enters it → status `Approved`.
7. Warehouse packs and ships. Rep enters tracking → status `Shipped`.
8. Vendor issues credit memo. Rep enters credit memo number + actual credit
   amount per line → status `Credited`.
9. `vendor_credit` row created, pushed to QBO as VendorCredit.

## W6. Daily reorder run

1. **Inventory → Low Stock**.
2. Click **Auto-tag below reorder**. 12 SKUs tagged.
3. Click **Create POs from Tagged** → wizard groups by vendor (3 vendors).
4. Review each draft PO; adjust qtys.
5. Confirm all → 3 Draft POs created.
6. Purchasing opens each, presses **Send PO** to email vendors.

## W7. Scrape HHP for new parts

1. **Tools → HHP Scraper**.
2. Click **Start Scan**. Phase 1 runs.
3. Phases 2 & 3 run automatically.
4. **Review** phase: table of new products. Reviewer accepts 30, edits 5,
   rejects 7.
5. **Upload** phase: 35 products created/updated. `scrape_runs` row written.

## W8. End-of-day

1. Manager opens **Dashboard**, reviews KPIs.
2. **Accounting → Aging AR**: prints reminders for 30+d overdue.
3. **Sync Center**: confirms queue depth = 0 and last successful sync recent.
4. **Tools → Backup**: one-click backup zip to `\\nas\backups\`.
5. Manager logs out.
