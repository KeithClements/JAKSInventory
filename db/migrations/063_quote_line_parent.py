"""Migration 063: Link warranty (and other add-on) lines to a parent line.

Adds:
- quote_lines.parent_line_id INTEGER NULL          (self-FK; child rolls up to parent)
- sales_order_lines.parent_line_id INTEGER NULL    (preserved through quote→SO conversion)
- invoice_lines.parent_line_id INTEGER NULL        (preserved through SO→invoice conversion)

Used by the new "warranty as optional add-on line" flow — each warranty
becomes its own quote line with ``parent_line_id`` set to the parent
product line and ``is_optional=1`` so the customer can decline it.

Cascade-on-delete behavior is enforced in application code, not via FK,
to keep migration-friendly across SQLite's limited ALTER TABLE.
"""


def migrate(conn):
    statements = [
        ("quote_lines", "parent_line_id", "INTEGER"),
        ("sales_order_lines", "parent_line_id", "INTEGER"),
        ("invoice_lines", "parent_line_id", "INTEGER"),
    ]
    for table, col, typedef in statements:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # already exists or table missing
    conn.commit()
