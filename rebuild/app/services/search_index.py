"""
app/services/search_index.py
============================
Precomputed normalized part-number columns for fast list/workspace search.

The product search matches part numbers across cross_references (216k+ rows),
product_vendor_sources, etc. by normalizing BOTH sides (strip separators, lower).
Doing that normalization as a SQL function on every row of every query
(``search_service._norm_col``) is O(rows × separators) per keystroke and became
multi-second once the catalog doubled.

Fix: store the normalized value once in an indexed column, kept in sync by
triggers, so the query reads a plain column (no per-row function eval). The
stored value is computed with the SAME separator set as ``_norm_col`` so search
results are byte-for-byte identical to before — only faster.

``ensure_search_norm_columns`` is idempotent: it adds the column + index if
missing and backfills any NULL rows. Ongoing sync on ORM writes is handled by
the ``before_insert``/``before_update`` listeners in app/models/product.py
(no DB trigger — that would double-write 216k rows during a bulk import).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Must match search_service._NORM_SEPARATORS exactly.
_NORM_SEPARATORS = ("-", " ", ".", "/", "_", "(", ")", "+", "#", "%")

# (table, source column, normalized column)
_TARGETS = [
    ("cross_references", "ref_number", "ref_number_norm"),
    ("product_vendor_sources", "vendor_part_number", "vendor_part_number_norm"),
    ("product_vendor_sources", "vendor_sku", "vendor_sku_norm"),
]


def _norm_sql(col: str) -> str:
    """SQL expression normalizing `col` the way _norm_col does (lower + strip
    each separator)."""
    expr = f"lower({col})"
    for sep in _NORM_SEPARATORS:
        s = sep.replace("'", "''")
        expr = f"replace({expr}, '{s}', '')"
    return expr


def _has_column(conn: Connection, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def ensure_search_norm_columns(conn: Connection) -> dict:
    """Idempotently add + populate + auto-maintain the normalized search columns.
    Returns a small summary. Caller owns the transaction/commit."""
    summary: dict[str, int] = {"added": 0, "backfilled": 0, "triggers": 0, "indexes": 0}
    for table, src, norm in _TARGETS:
        if not _has_column(conn, table, norm):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {norm} TEXT"))
            summary["added"] += 1

        norm_expr = _norm_sql(src)
        # Backfill rows not yet computed (NULL) — cheap no-op once populated.
        res = conn.execute(text(
            f"UPDATE {table} SET {norm} = {norm_expr} WHERE {norm} IS NULL"))
        summary["backfilled"] += res.rowcount or 0

        # Index for prefix lookups + a faster plain scan for substring.
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_{norm} ON {table}({norm})"))
        summary["indexes"] += 1
    return summary
