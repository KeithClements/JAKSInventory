"""Migration 083: product fitments (engine × make × year × model).

T1.3 (industry-comparison Tier 1). The industry-standard ACES/PIES
fitment record has roughly this shape. We keep it pragmatically narrow
and let advanced attributes go in ``notes`` rather than building a
fully-normalized application table from day one.

Columns:
  id              PK
  product_id      FK products.id  (CASCADE — fitment dies with product)
  make            truck/equipment make (e.g. 'Cummins', 'Detroit', 'Mack')
  model           model designator   (e.g. 'ISX15', 'DD15', 'MP8')
  year_from       inclusive earliest year (NULL = unbounded back)
  year_to         inclusive latest year   (NULL = current)
  engine_family   broad family (e.g. 'X15', 'Series 60')
  engine_model    specific engine model
  position        installation position (e.g. 'driver-side', 'rear axle')
  notes           free-form (ACES sub-model, transmission, etc.)
  source          'manual' | 'scraper' | 'ai' | 'vendor'
  confidence      0-100 (NULL=unrated)
  created_by      user
  created_at      timestamp
  updated_at      timestamp
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
    if _table_exists(conn, "product_fitments"):
        return
    try:
        conn.execute(
            """
            CREATE TABLE product_fitments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                make          TEXT,
                model         TEXT,
                year_from     INTEGER,
                year_to       INTEGER,
                engine_family TEXT,
                engine_model  TEXT,
                position      TEXT,
                notes         TEXT,
                source        TEXT DEFAULT 'manual',
                confidence    INTEGER,
                created_by    TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    except Exception:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_fitments (
                id            SERIAL PRIMARY KEY,
                product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                make          TEXT,
                model         TEXT,
                year_from     INTEGER,
                year_to       INTEGER,
                engine_family TEXT,
                engine_model  TEXT,
                position      TEXT,
                notes         TEXT,
                source        TEXT DEFAULT 'manual',
                confidence    INTEGER,
                created_by    TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_fitments_product ON product_fitments(product_id)",
        "CREATE INDEX IF NOT EXISTS ix_fitments_make    ON product_fitments(make)",
        "CREATE INDEX IF NOT EXISTS ix_fitments_model   ON product_fitments(model)",
        "CREATE INDEX IF NOT EXISTS ix_fitments_engine  ON product_fitments(engine_model)",
        "CREATE INDEX IF NOT EXISTS ix_fitments_year    ON product_fitments(year_from, year_to)",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.commit()
