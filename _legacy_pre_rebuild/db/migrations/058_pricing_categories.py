"""Migration 058: Category-based tiered pricing.

Introduces a category × cost-band → margin model with per-customer-tier
discounts. Replaces the flat `price_tiers` / `customer_pricing` tables
from migration 008 going forward (legacy tables are kept dormant for
back-compat).

Schema:
  - price_categories          (A..E, name, default flag)
  - price_category_bands      (cost_min, cost_max NULL=∞, margin_pct)
  - customer_tiers            (Standard, Silver, Gold, ...)
  - customer_tier_discounts   (tier × category → discount_pct matrix)
  - manufacturer_categories   (manufacturer_name → price_category_id)

Plus columns:
  - products.price_category_id   (override; NULL = inherit from manufacturer)
  - customers.tier_id            (FK customer_tiers; NULL = default tier)

A default category ("A — Default") and default tier ("Standard") are
seeded so all existing products + customers pick up valid pricing
without further configuration.
"""
from __future__ import annotations


def _column_exists(conn, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return False
    for r in rows:
        # SQLite Row or tuple — column name is index 1
        try:
            name = r["name"] if hasattr(r, "keys") else r[1]
        except Exception:
            name = r[1] if len(r) > 1 else None
        if name == column:
            return True
    return False


def migrate(conn) -> None:
    # ── Categories ────────────────────────────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL UNIQUE,
            name        TEXT    NOT NULL,
            description TEXT    DEFAULT '',
            sort_order  INTEGER DEFAULT 0,
            is_default  INTEGER DEFAULT 0,
            active      INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT (datetime('now')),
            updated_at  TEXT    DEFAULT (datetime('now'))
        )
        """
    )

    # ── Cost bands per category ───────────────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_category_bands (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id  INTEGER NOT NULL REFERENCES price_categories(id) ON DELETE CASCADE,
            cost_min     REAL    NOT NULL DEFAULT 0,
            cost_max     REAL,                          -- NULL = ∞
            margin_pct   REAL    NOT NULL DEFAULT 0,
            sort_order   INTEGER DEFAULT 0,
            UNIQUE(category_id, cost_min)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bands_category ON price_category_bands(category_id, cost_min)"
    )

    # ── Customer tiers ────────────────────────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_tiers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            description TEXT    DEFAULT '',
            sort_order  INTEGER DEFAULT 0,
            is_default  INTEGER DEFAULT 0,
            active      INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT (datetime('now')),
            updated_at  TEXT    DEFAULT (datetime('now'))
        )
        """
    )

    # ── Tier × Category discount matrix ───────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_tier_discounts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            tier_id      INTEGER NOT NULL REFERENCES customer_tiers(id) ON DELETE CASCADE,
            category_id  INTEGER NOT NULL REFERENCES price_categories(id) ON DELETE CASCADE,
            discount_pct REAL    NOT NULL DEFAULT 0,
            UNIQUE(tier_id, category_id)
        )
        """
    )

    # ── Manufacturer → category mapping ───────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manufacturer_categories (
            manufacturer_name TEXT    PRIMARY KEY,
            category_id       INTEGER NOT NULL REFERENCES price_categories(id) ON DELETE CASCADE,
            updated_at        TEXT    DEFAULT (datetime('now'))
        )
        """
    )

    # ── New columns on existing tables ────────────────────────────────
    if not _column_exists(conn, "products", "price_category_id"):
        conn.execute("ALTER TABLE products ADD COLUMN price_category_id INTEGER")
    if not _column_exists(conn, "customers", "tier_id"):
        conn.execute("ALTER TABLE customers ADD COLUMN tier_id INTEGER")

    # ── Seed defaults ────────────────────────────────────────────────
    cur = conn.execute("SELECT COUNT(*) FROM price_categories")
    if cur.fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO price_categories (code, name, description, sort_order, is_default) VALUES (?, ?, ?, ?, ?)",
            [
                ("A", "Category A", "Default category — assign manufacturers here", 1, 1),
                ("B", "Category B", "", 2, 0),
                ("C", "Category C", "", 3, 0),
                ("D", "Category D", "", 4, 0),
                ("E", "Category E", "", 5, 0),
            ],
        )
        # Seed a single default band per category at 0%
        cat_rows = conn.execute("SELECT id FROM price_categories ORDER BY sort_order").fetchall()
        for r in cat_rows:
            cid = r[0] if isinstance(r, tuple) else r["id"]
            conn.execute(
                "INSERT INTO price_category_bands (category_id, cost_min, cost_max, margin_pct, sort_order) VALUES (?, ?, ?, ?, ?)",
                (cid, 0.0, None, 0.0, 0),
            )

    cur = conn.execute("SELECT COUNT(*) FROM customer_tiers")
    if cur.fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO customer_tiers (name, description, sort_order, is_default) VALUES (?, ?, ?, ?)",
            [
                ("Standard", "Default tier — 0% discount across categories", 1, 1),
                ("Silver",   "",                                              2, 0),
                ("Gold",     "",                                              3, 0),
                ("Platinum", "",                                              4, 0),
            ],
        )

    conn.commit()
