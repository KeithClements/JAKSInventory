"""Add account_number, ship-to / bill-to address fields to customers."""

from __future__ import annotations


def migrate(conn) -> None:
    # ── account_number (auto-assigned, unique) ──
    try:
        conn.execute("ALTER TABLE customers ADD COLUMN account_number TEXT")
    except Exception:
        pass  # column already exists

    # ── Ship-to address fields ──
    for col in (
        "ship_to_address TEXT",
        "ship_to_city TEXT",
        "ship_to_state TEXT",
        "ship_to_zip TEXT",
    ):
        try:
            conn.execute(f"ALTER TABLE customers ADD COLUMN {col}")
        except Exception:
            pass

    # ── Bill-to address fields ──
    for col in (
        "bill_to_address TEXT",
        "bill_to_city TEXT",
        "bill_to_state TEXT",
        "bill_to_zip TEXT",
    ):
        try:
            conn.execute(f"ALTER TABLE customers ADD COLUMN {col}")
        except Exception:
            pass

    conn.commit()

    # ── Auto-assign account numbers to existing customers who lack one ──
    rows = conn.execute(
        "SELECT id FROM customers WHERE account_number IS NULL ORDER BY id"
    ).fetchall()

    # Starting account number
    start = 10001
    # Check for highest existing account_number
    max_row = conn.execute(
        "SELECT MAX(CAST(account_number AS INTEGER)) FROM customers "
        "WHERE account_number IS NOT NULL AND account_number != ''"
    ).fetchone()
    if max_row and max_row[0]:
        start = int(max_row[0]) + 1

    for i, row in enumerate(rows):
        cust_id = row[0] if isinstance(row, (list, tuple)) else row["id"]
        acct = str(start + i)
        conn.execute(
            "UPDATE customers SET account_number = %s WHERE id = %s",
            (acct, cust_id),
        )

    conn.commit()
