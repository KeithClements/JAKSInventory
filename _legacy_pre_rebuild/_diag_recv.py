from db import get_db

with get_db() as conn:
    print("--- Recent POs ---")
    for r in conn.execute("SELECT id, po_number, status FROM purchase_orders ORDER BY id DESC LIMIT 5").fetchall():
        print(dict(r) if hasattr(r, "keys") else r)

    print("\n--- Recent PO lines ---")
    for r in conn.execute(
        "SELECT id, po_id, product_id, sku, line_kind, quantity_ordered, quantity_received "
        "FROM purchase_order_lines ORDER BY id DESC LIMIT 12"
    ).fetchall():
        print(dict(r) if hasattr(r, "keys") else r)

    print("\n--- Recent inventory transactions ---")
    for r in conn.execute(
        "SELECT id, product_id, txn_type, quantity, created_at, notes "
        "FROM inventory_transactions ORDER BY id DESC LIMIT 10"
    ).fetchall():
        print(dict(r) if hasattr(r, "keys") else r)

    print("\n--- Products created today ---")
    for r in conn.execute(
        "SELECT id, sku, title, qty_on_hand FROM products "
        "WHERE created_at >= date('now','-1 day') ORDER BY id DESC LIMIT 20"
    ).fetchall():
        print(dict(r) if hasattr(r, "keys") else r)
