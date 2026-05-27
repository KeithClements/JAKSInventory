"""Backfill missing inventory transactions for PO lines that were marked
received but never produced an inventory adjustment (due to the duplicate-sku
column bug in receive_po_line). Idempotent: skips lines that already have
matching 'receive' transactions covering the received qty."""

from db import get_db
from db.inventory import adjust_product_quantity


def main():
    fixes = []
    skipped = []
    errors = []

    with get_db() as conn:
        rows = conn.execute("""
            SELECT pol.id AS line_id,
                   pol.po_id,
                   pol.product_id,
                   pol.quantity_received,
                   COALESCE(p.sku, pol.sku)               AS sku,
                   COALESCE(p.title, pol.description)     AS title,
                   po.po_number,
                   po.status                              AS po_status,
                   COALESCE(pol.line_kind, 'product')     AS line_kind
            FROM purchase_order_lines pol
            LEFT JOIN products p           ON pol.product_id = p.id
            JOIN purchase_orders po        ON pol.po_id = po.id
            WHERE COALESCE(pol.quantity_received, 0) > 0
              AND LOWER(COALESCE(pol.line_kind,'product')) NOT IN ('core','discount')
              AND pol.product_id IS NOT NULL
            ORDER BY pol.id
        """).fetchall()

        for r in rows:
            d = dict(r)
            pid = d["product_id"]
            qty = int(d["quantity_received"] or 0)
            po_num = d["po_number"]
            sku = d["sku"]
            already = conn.execute(
                """SELECT COALESCE(SUM(quantity_change),0)
                   FROM inventory_transactions
                   WHERE product_id = %s AND reference = %s AND transaction_type = 'receive'""",
                (pid, po_num),
            ).fetchone()
            already_qty = int((already[0] if not hasattr(already, "keys") else already[0]) or 0)
            missing = qty - already_qty
            if missing <= 0:
                skipped.append((d["line_id"], po_num, sku, qty, already_qty))
                continue
            if not sku:
                errors.append((d["line_id"], po_num, "no sku resolvable"))
                continue
            res = adjust_product_quantity(
                sku=sku,
                delta=missing,
                txn_type="receive",
                reference=po_num,
                notes=f"Backfill: PO {po_num} received but txn not logged (duplicate-sku-column bug)",
            )
            if res.get("success"):
                fixes.append((d["line_id"], po_num, sku, missing, res.get("qty_before"), res.get("qty_after")))
            else:
                errors.append((d["line_id"], po_num, res.get("error")))

    print(f"\n=== Backfilled {len(fixes)} line(s) ===")
    for f in fixes:
        print(f"  line {f[0]}  {f[1]}  {f[2]}  +{f[3]}  ({f[4]} -> {f[5]})")
    print(f"\n=== Skipped {len(skipped)} (already had matching txns) ===")
    for s in skipped:
        print(f"  line {s[0]}  {s[1]}  {s[2]}  qty_received={s[3]} already_logged={s[4]}")
    if errors:
        print(f"\n=== {len(errors)} ERROR(S) ===")
        for e in errors:
            print(f"  line {e[0]}  {e[1]}  {e[2]}")


if __name__ == "__main__":
    main()
