"""Migration 033: Expand customer profile fields."""


def migrate(conn):
    new_cols = [
        ("tax_id", "TEXT DEFAULT ''"),
        ("tax_id_image_path", "TEXT DEFAULT ''"),
        ("tax_id_expiration", "TEXT DEFAULT ''"),
        ("birthday", "TEXT DEFAULT ''"),
        ("special_dates", "TEXT DEFAULT ''"),
        ("mobile_phone", "TEXT DEFAULT ''"),
        ("business_phone", "TEXT DEFAULT ''"),
        ("text_permission", "INTEGER DEFAULT 0"),
        ("delivery_notes", "TEXT DEFAULT ''"),
        ("pricing_tier", "TEXT DEFAULT 'Standard'"),
        ("customer_ranking", "TEXT DEFAULT ''"),
    ]
    for col, typedef in new_cols:
        try:
            conn.execute(f"ALTER TABLE customers ADD COLUMN {col} {typedef}")
        except Exception:
            pass

    conn.commit()
