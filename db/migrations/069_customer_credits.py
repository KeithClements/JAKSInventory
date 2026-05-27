"""Migration 069: Customer credits ledger.

Tracks issued / open / applied credit memos owed to a customer. Required
so that "Issue Credit" on a returned core (and future RMAs / manual
adjustments) actually shows up on the customer's account and can be
applied against an outstanding invoice.

Schema (SQLite — Postgres equivalents are produced via the runtime
schema synchronizer):

    customer_credits(
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id             INTEGER NOT NULL,
        source_type             TEXT    NOT NULL,
                                  -- 'core' | 'rma' | 'manual'
        source_id               INTEGER,
                                  -- core_returns.id / rma.id / NULL
        amount                  REAL    NOT NULL DEFAULT 0,
        applied_to_invoice_id   INTEGER,
        issue_date              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        applied_date            TIMESTAMP,
        status                  TEXT    NOT NULL DEFAULT 'open',
                                  -- 'open' | 'applied' | 'voided'
        notes                   TEXT,
        created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(customer_id) REFERENCES customers(id),
        FOREIGN KEY(applied_to_invoice_id) REFERENCES invoices(id)
    )

Backfill: every ``core_returns`` row whose ``status='credited'`` and
which does not yet have a paired credit row gets one created.
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
        # Postgres path
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
    if not _table_exists(conn, "customer_credits"):
        try:
            conn.execute(
                """
                CREATE TABLE customer_credits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id INTEGER,
                    amount REAL NOT NULL DEFAULT 0,
                    applied_to_invoice_id INTEGER,
                    issue_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    applied_date TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'open',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(customer_id) REFERENCES customers(id),
                    FOREIGN KEY(applied_to_invoice_id) REFERENCES invoices(id)
                )
                """
            )
        except Exception:
            # Postgres fallback (SERIAL instead of AUTOINCREMENT).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS customer_credits (
                    id SERIAL PRIMARY KEY,
                    customer_id INTEGER NOT NULL REFERENCES customers(id),
                    source_type TEXT NOT NULL,
                    source_id INTEGER,
                    amount NUMERIC(10,2) NOT NULL DEFAULT 0,
                    applied_to_invoice_id INTEGER REFERENCES invoices(id),
                    issue_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    applied_date TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'open',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    # Indexes for the common lookups.
    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_customer_credits_customer_status "
        "ON customer_credits(customer_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_customer_credits_source "
        "ON customer_credits(source_type, source_id)",
        "CREATE INDEX IF NOT EXISTS ix_customer_credits_applied_invoice "
        "ON customer_credits(applied_to_invoice_id)",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass

    # Backfill open credits for already-credited cores that lack a ledger row.
    if _table_exists(conn, "core_returns"):
        try:
            conn.execute(
                """
                INSERT INTO customer_credits (
                    customer_id, source_type, source_id, amount,
                    issue_date, status, notes
                )
                SELECT cr.customer_id,
                       'core' AS source_type,
                       cr.id  AS source_id,
                       COALESCE(cr.customer_core_price, cr.core_charge, 0)
                           AS amount,
                       COALESCE(cr.credit_issued_date, cr.updated_at,
                                CURRENT_TIMESTAMP) AS issue_date,
                       'open' AS status,
                       'Backfilled from core_returns at migration 069'
                FROM core_returns cr
                WHERE cr.status = 'credited'
                  AND cr.customer_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM customer_credits cc
                       WHERE cc.source_type = 'core'
                         AND cc.source_id = cr.id
                  )
                """
            )
        except Exception:
            # If core_returns has a different shape on this DB, skip the
            # backfill rather than blocking startup. Manual entry remains
            # available via the customer detail screen.
            pass

    conn.commit()
