"""Migration 084: part supersessions / replacement chains.

T1.4 (industry-comparison Tier 1). When a manufacturer obsoletes a
part, sales reps need to instantly find the replacement — and walk
multi-hop chains (A → B → C → D) to the *current* part.

Columns:
  id              PK
  old_product_id  FK products.id  (CASCADE) — the obsolete part
  new_product_id  FK products.id  (CASCADE) — what replaces it
  direction       'replaces' (new replaces old) or 'replaced_by'
                  (informational alias, redundant but mirrors how
                  reps think about it).
  effective_date  optional date the supersession took effect
  reason          'obsolete' | 'redesign' | 'consolidation' |
                  'recall' | 'vendor_change' | other free text
  notes           free-form
  created_by      user
  created_at      timestamp

Uniqueness: (old_product_id, new_product_id) — same pair only once.
Cycle prevention is enforced at the helper layer, not in SQL.
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
    if _table_exists(conn, "part_supersessions"):
        return
    try:
        conn.execute(
            """
            CREATE TABLE part_supersessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                old_product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                new_product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                direction       TEXT DEFAULT 'replaces',
                effective_date  TEXT,
                reason          TEXT,
                notes           TEXT,
                created_by      TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (old_product_id, new_product_id)
            )
            """
        )
    except Exception:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS part_supersessions (
                id              SERIAL PRIMARY KEY,
                old_product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                new_product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                direction       TEXT DEFAULT 'replaces',
                effective_date  TEXT,
                reason          TEXT,
                notes           TEXT,
                created_by      TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (old_product_id, new_product_id)
            )
            """
        )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_super_old ON part_supersessions(old_product_id)",
        "CREATE INDEX IF NOT EXISTS ix_super_new ON part_supersessions(new_product_id)",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.commit()
