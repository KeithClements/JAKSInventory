"""Migration 080: Lost-sales capture table.

F1: records customer demand that didn't convert — so we can drive
reorder decisions, identify pricing leakage, and spot competitors
winning deals. Voluntarily populated by the user when canceling a
quote, declining a special-order request, or noting "no-stock"
walk-aways.

Columns:
  id              auto-increment PK
  customer_id     optional FK → customers
  product_id      optional FK → products (NULL for unstocked SKUs)
  sku_requested   verbatim text typed at the counter (always preserved)
  description     short free-form description
  quantity        qty requested (default 1)
  quoted_price    last quoted unit price (0 = unknown)
  reason          'no_stock' | 'price' | 'lead_time' | 'competitor' |
                  'no_xref' | 'other'
  source_quote_id optional FK → quotes (when promoted from a quote)
  notes           free-form
  lost_date       date (default today)
  created_by      user
  created_at      timestamp
"""
from __future__ import annotations


def _table_exists(conn, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return bool(row)
    except Exception:
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = %s",
                (name,),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def migrate(conn) -> None:
    if _table_exists(conn, "lost_sales"):
        return
    try:
        conn.execute(
            """
            CREATE TABLE lost_sales (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id     INTEGER,
                product_id      INTEGER,
                sku_requested   TEXT,
                description     TEXT,
                quantity        INTEGER DEFAULT 1,
                quoted_price    REAL DEFAULT 0,
                reason          TEXT,
                source_quote_id INTEGER,
                notes           TEXT,
                lost_date       DATE DEFAULT CURRENT_DATE,
                created_by      TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(customer_id) REFERENCES customers(id),
                FOREIGN KEY(product_id) REFERENCES products(id),
                FOREIGN KEY(source_quote_id) REFERENCES quotes(id)
            )
            """
        )
    except Exception:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lost_sales (
                id              SERIAL PRIMARY KEY,
                customer_id     INTEGER REFERENCES customers(id),
                product_id      INTEGER REFERENCES products(id),
                sku_requested   TEXT,
                description     TEXT,
                quantity        INTEGER DEFAULT 1,
                quoted_price    NUMERIC(10,2) DEFAULT 0,
                reason          TEXT,
                source_quote_id INTEGER REFERENCES quotes(id),
                notes           TEXT,
                lost_date       DATE DEFAULT CURRENT_DATE,
                created_by      TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_lost_sales_customer ON lost_sales(customer_id)",
        "CREATE INDEX IF NOT EXISTS ix_lost_sales_product ON lost_sales(product_id)",
        "CREATE INDEX IF NOT EXISTS ix_lost_sales_reason ON lost_sales(reason)",
        "CREATE INDEX IF NOT EXISTS ix_lost_sales_date ON lost_sales(lost_date)",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass

    conn.commit()
