"""Migration 057: tax_rates table for manual sales-tax rate lookup.

Stores per-state and optional per-ZIP rates for jurisdictions where the
business has nexus. Used by the invoice dialog to auto-apply tax based
on the customer's ship-to state/ZIP. Most-specific ZIP match wins; the
state default (zip IS NULL) is the fallback. States not present in the
table imply no nexus → 0% tax.

Schema:
  - id          INTEGER PRIMARY KEY
  - state       TEXT     (2-char code, e.g. 'CO')
  - zip         TEXT     (5-digit, NULL = state default)
  - rate        REAL     (decimal, e.g. 0.029 = 2.9%)
  - description TEXT
  - active      INTEGER  DEFAULT 1
"""
from __future__ import annotations


def migrate(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tax_rates (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            state        TEXT    NOT NULL,
            zip          TEXT,
            rate         REAL    NOT NULL DEFAULT 0,
            description  TEXT    DEFAULT '',
            active       INTEGER DEFAULT 1,
            created_at   TEXT    DEFAULT (datetime('now')),
            updated_at   TEXT    DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tax_rates_state_zip ON tax_rates(state, zip)"
    )

    # Seed Colorado + Wyoming state defaults if table is empty.
    cur = conn.execute("SELECT COUNT(*) FROM tax_rates")
    if cur.fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO tax_rates (state, zip, rate, description) VALUES (?, ?, ?, ?)",
            [
                ("CO", None, 0.029, "Colorado state default (2.9%) — add city/county overrides"),
                ("WY", None, 0.04,  "Wyoming state default (4.0%) — add city/county overrides"),
            ],
        )

    conn.commit()
