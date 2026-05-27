"""Migration 075: Backfill core_returns.customer_id from parent invoice.

Phase B.2: ensures every ``core_returns`` row carries the customer_id of
its parent invoice so credits posted via ``issue_core_credit`` always land
on the correct customer ledger. Also backfills ledger rows for any
already-credited cores that were missing one (shouldn't normally exist —
migration 069 covered them — but is cheap belt-and-braces).

Idempotent: only updates rows where ``customer_id IS NULL`` and the
parent invoice has a non-NULL customer.
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
    if not _table_exists(conn, "core_returns"):
        return

    # 1. Backfill NULL customer_id from parent invoice.
    try:
        conn.execute(
            """
            UPDATE core_returns
               SET customer_id = (
                       SELECT i.customer_id
                         FROM invoices i
                        WHERE i.id = core_returns.invoice_id
                   ),
                   updated_at = CURRENT_TIMESTAMP
             WHERE customer_id IS NULL
               AND invoice_id IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM invoices i
                    WHERE i.id = core_returns.invoice_id
                      AND i.customer_id IS NOT NULL
               )
            """
        )
    except Exception:
        # Postgres has the same syntax for correlated UPDATE — leave best
        # effort and fall through.
        pass

    # 2. Re-run the core-credit ledger backfill (mirror of migration 069
    #    so that any cores credited between 069 and now without a ledger
    #    row get one). Safe — guarded by NOT EXISTS.
    if _table_exists(conn, "customer_credits"):
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
                       COALESCE(cr.credit_issued,
                                cr.customer_core_price,
                                cr.core_charge, 0) AS amount,
                       COALESCE(cr.credit_date, cr.updated_at,
                                CURRENT_TIMESTAMP) AS issue_date,
                       'open' AS status,
                       'Backfilled by migration 075'
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
            pass

    conn.commit()
