"""Migration 040 – Suggested Sells: link products that are commonly
purchased together (e.g. cylinder head → headbolts + head gasket)."""
from __future__ import annotations


def migrate(conn) -> None:
    stmts = [
        """CREATE TABLE IF NOT EXISTS suggested_sells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            suggested_product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            relationship_type TEXT NOT NULL DEFAULT 'recommended',
            notes TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, suggested_product_id)
        )""",
        """CREATE INDEX IF NOT EXISTS idx_suggested_sells_product
           ON suggested_sells(product_id)""",
        """CREATE INDEX IF NOT EXISTS idx_suggested_sells_suggested
           ON suggested_sells(suggested_product_id)""",
    ]
    for sql in stmts:
        conn.execute(sql)
    conn.commit()
