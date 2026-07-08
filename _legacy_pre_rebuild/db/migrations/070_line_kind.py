"""Migration 070: ``line_kind`` discriminator on quote / SO / invoice lines.

UAT round 1 surfaced that warranty add-on lines (created via the
🛡 Warranty button on a quote line) appear in places that should only
list real inventoried products — PO line pickers, inventory views, etc.

Today the only way to tell warranty / core / fee rows apart from real
product rows is to sniff the SKU prefix or description, which is
fragile. This migration introduces a stable discriminator:

    line_kind TEXT NOT NULL DEFAULT 'product'
        -- 'product' | 'core' | 'warranty' | 'fee'

…on quote_lines, sales_order_lines, and invoice_lines, and backfills
existing rows from the heuristics that have served until now:

* ``parent_line_id IS NOT NULL`` AND description LIKE 'Extended Warranty%'
  → 'warranty'
* SKU starts with 'CORE-' OR description starts with 'Core charge:'
  → 'core'
* Otherwise → 'product'

Future fee-style synthetic lines (shipping, fuel surcharge, CC
convenience) can adopt 'fee' incrementally.
"""
from __future__ import annotations


def _has_column(conn, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            name = r[1] if not hasattr(r, "keys") else r["name"]
            if str(name) == column:
                return True
        return False
    except Exception:
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=%s AND column_name=%s",
                (table, column),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


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
                "SELECT 1 FROM information_schema.tables WHERE table_name=%s",
                (name,),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


_TABLES = ("quote_lines", "sales_order_lines", "invoice_lines")


def migrate(conn) -> None:
    for tbl in _TABLES:
        if not _table_exists(conn, tbl):
            continue
        if not _has_column(conn, tbl, "line_kind"):
            try:
                conn.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN line_kind TEXT "
                    f"NOT NULL DEFAULT 'product'"
                )
            except Exception:
                # Some SQLite ALTER paths refuse NOT NULL DEFAULT; retry without.
                try:
                    conn.execute(
                        f"ALTER TABLE {tbl} ADD COLUMN line_kind TEXT "
                        f"DEFAULT 'product'"
                    )
                except Exception:
                    continue

        # Backfill warranty
        try:
            conn.execute(
                f"""
                UPDATE {tbl}
                   SET line_kind = 'warranty'
                 WHERE (line_kind IS NULL OR line_kind = '' OR line_kind = 'product')
                   AND parent_line_id IS NOT NULL
                   AND (
                       description LIKE 'Extended Warranty%%'
                       OR description LIKE '%%Warranty —%%'
                   )
                """
            )
        except Exception:
            pass

        # Backfill core. quote_lines has no 'sku' column, so only join SKU
        # heuristics on tables that do.
        has_sku = _has_column(conn, tbl, "sku")
        try:
            if has_sku:
                conn.execute(
                    f"""
                    UPDATE {tbl}
                       SET line_kind = 'core'
                     WHERE (line_kind IS NULL OR line_kind = '' OR line_kind = 'product')
                       AND (
                           UPPER(COALESCE(sku, '')) LIKE 'CORE-%%'
                           OR description LIKE 'Core charge:%%'
                       )
                    """
                )
            else:
                conn.execute(
                    f"""
                    UPDATE {tbl}
                       SET line_kind = 'core'
                     WHERE (line_kind IS NULL OR line_kind = '' OR line_kind = 'product')
                       AND description LIKE 'Core charge:%%'
                    """
                )
        except Exception:
            pass

        # Normalize NULLs to 'product'
        try:
            conn.execute(
                f"UPDATE {tbl} SET line_kind = 'product' "
                f"WHERE line_kind IS NULL OR line_kind = ''"
            )
        except Exception:
            pass

        # Helpful filter index.
        try:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{tbl}_line_kind "
                f"ON {tbl}(line_kind)"
            )
        except Exception:
            pass

    conn.commit()


# Aliases the auto-discovery layer may probe.
def up(conn):
    migrate(conn)


def run(conn):
    migrate(conn)


def run_migration(conn):
    migrate(conn)
