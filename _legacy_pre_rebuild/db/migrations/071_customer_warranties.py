"""Migration 071: ``customer_warranties`` certificate ledger.

Mirrors the ``customer_credits`` (mig 069) pattern — gives every
warranty add-on sold a row of its own that we can list on the customer
account, expire automatically, mark as claimed, or void.

Schema (SQLite — Postgres equivalent produced via the runtime schema
synchronizer):

    customer_warranties(
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id          INTEGER NOT NULL,
        invoice_id           INTEGER NOT NULL,
        invoice_line_id      INTEGER,
        product_id           INTEGER,
        sku                  TEXT,
        description          TEXT,
        qty                  INTEGER NOT NULL DEFAULT 1,
        coverage_type        TEXT NOT NULL DEFAULT 'parts_only',
                                -- 'parts_only' | 'parts_labor'
        coverage_months      INTEGER NOT NULL DEFAULT 0,
        mileage_limit        INTEGER,            -- NULL = unlimited
        start_date           DATE,
        end_date             DATE,
        certificate_number   TEXT UNIQUE,        -- 'CRT-{invoice}-{seq}'
        status               TEXT NOT NULL DEFAULT 'active',
                                -- 'active' | 'claimed' | 'expired' | 'voided'
        amount_charged       REAL DEFAULT 0,
        claim_count          INTEGER NOT NULL DEFAULT 0,
        notes                TEXT,
        created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(customer_id) REFERENCES customers(id),
        FOREIGN KEY(invoice_id) REFERENCES invoices(id),
        FOREIGN KEY(invoice_line_id) REFERENCES invoice_lines(id)
    )

Backfill: every finalized invoice_line with ``warranty_months > 0``
(plus its parent line for SKU / description) gets a row, marked
'active' or 'expired' based on ``end_date < today``.
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
                "SELECT 1 FROM information_schema.tables WHERE table_name=%s",
                (name,),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def _has_column(conn, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            name = r[1] if not hasattr(r, "keys") else r["name"]
            if str(name) == column:
                return True
        return False
    except Exception:
        return False


def migrate(conn) -> None:
    if not _table_exists(conn, "customer_warranties"):
        try:
            conn.execute(
                """
                CREATE TABLE customer_warranties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER NOT NULL,
                    invoice_id INTEGER NOT NULL,
                    invoice_line_id INTEGER,
                    product_id INTEGER,
                    sku TEXT,
                    description TEXT,
                    qty INTEGER NOT NULL DEFAULT 1,
                    coverage_type TEXT NOT NULL DEFAULT 'parts_only',
                    coverage_months INTEGER NOT NULL DEFAULT 0,
                    mileage_limit INTEGER,
                    start_date DATE,
                    end_date DATE,
                    certificate_number TEXT UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    amount_charged REAL DEFAULT 0,
                    claim_count INTEGER NOT NULL DEFAULT 0,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(customer_id) REFERENCES customers(id),
                    FOREIGN KEY(invoice_id) REFERENCES invoices(id),
                    FOREIGN KEY(invoice_line_id) REFERENCES invoice_lines(id)
                )
                """
            )
        except Exception:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS customer_warranties (
                    id SERIAL PRIMARY KEY,
                    customer_id INTEGER NOT NULL REFERENCES customers(id),
                    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
                    invoice_line_id INTEGER REFERENCES invoice_lines(id),
                    product_id INTEGER,
                    sku TEXT,
                    description TEXT,
                    qty INTEGER NOT NULL DEFAULT 1,
                    coverage_type TEXT NOT NULL DEFAULT 'parts_only',
                    coverage_months INTEGER NOT NULL DEFAULT 0,
                    mileage_limit INTEGER,
                    start_date DATE,
                    end_date DATE,
                    certificate_number TEXT UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    amount_charged NUMERIC(10,2) DEFAULT 0,
                    claim_count INTEGER NOT NULL DEFAULT 0,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_customer_warranties_customer_status "
        "ON customer_warranties(customer_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_customer_warranties_invoice_line "
        "ON customer_warranties(invoice_line_id)",
        "CREATE INDEX IF NOT EXISTS ix_customer_warranties_certificate "
        "ON customer_warranties(certificate_number)",
        "CREATE INDEX IF NOT EXISTS ix_customer_warranties_end_date "
        "ON customer_warranties(end_date)",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass

    # Backfill: walk every finalized invoice that has warranty lines and
    # create a certificate row per warranty line. Keyed by invoice_line_id
    # so reruns are idempotent.
    if _table_exists(conn, "invoice_lines") and _has_column(
        conn, "invoice_lines", "warranty_months"
    ):
        try:
            rows = conn.execute(
                """
                SELECT il.id              AS invoice_line_id,
                       il.invoice_id      AS invoice_id,
                       il.product_id      AS product_id,
                       il.sku             AS sku,
                       il.description     AS description,
                       il.quantity        AS qty,
                       il.unit_price      AS amount_charged,
                       il.warranty_months AS months,
                       il.warranty_type   AS wtype,
                       il.warranty_mileage_limit AS mileage,
                       i.customer_id      AS customer_id,
                       i.invoice_number   AS invoice_number,
                       i.invoice_date     AS invoice_date
                  FROM invoice_lines il
                  JOIN invoices i ON i.id = il.invoice_id
                 WHERE COALESCE(il.warranty_months, 0) > 0
                   AND COALESCE(i.status, '') IN ('sent','paid','partial')
                   AND i.customer_id IS NOT NULL
                """
            ).fetchall()
        except Exception:
            rows = []

        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {}
            line_id = d.get("invoice_line_id")
            try:
                exists = conn.execute(
                    "SELECT 1 FROM customer_warranties WHERE invoice_line_id = ?",
                    (line_id,),
                ).fetchone()
            except Exception:
                try:
                    exists = conn.execute(
                        "SELECT 1 FROM customer_warranties WHERE invoice_line_id = %s",
                        (line_id,),
                    ).fetchone()
                except Exception:
                    exists = None
            if exists:
                continue

            months = int(d.get("months") or 0)
            inv_date = d.get("invoice_date")
            # Compute end_date = invoice_date + months. Stored as ISO date.
            try:
                from datetime import date, datetime, timedelta

                if isinstance(inv_date, str):
                    start = datetime.strptime(inv_date[:10], "%Y-%m-%d").date()
                elif hasattr(inv_date, "year"):
                    start = inv_date if isinstance(inv_date, date) else inv_date.date()
                else:
                    start = date.today()
                # add months by days approximation: 30.4375 * months
                end = start + timedelta(days=int(round(30.4375 * months)))
                today = date.today()
                status = "expired" if end < today else "active"
                start_iso = start.isoformat()
                end_iso = end.isoformat()
            except Exception:
                start_iso = None
                end_iso = None
                status = "active"

            inv_num = d.get("invoice_number") or d.get("invoice_id")
            cert = f"CRT-{inv_num}-{line_id}"

            try:
                conn.execute(
                    """
                    INSERT INTO customer_warranties (
                        customer_id, invoice_id, invoice_line_id,
                        product_id, sku, description, qty,
                        coverage_type, coverage_months, mileage_limit,
                        start_date, end_date, certificate_number,
                        status, amount_charged, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        d.get("customer_id"),
                        d.get("invoice_id"),
                        line_id,
                        d.get("product_id"),
                        d.get("sku"),
                        d.get("description"),
                        int(d.get("qty") or 1),
                        d.get("wtype") or "parts_only",
                        months,
                        d.get("mileage"),
                        start_iso,
                        end_iso,
                        cert,
                        status,
                        float(d.get("amount_charged") or 0),
                        "Backfilled at migration 071",
                    ),
                )
            except Exception:
                # On conflict / shape mismatch, keep going.
                pass

    conn.commit()


def up(conn):
    migrate(conn)


def run(conn):
    migrate(conn)


def run_migration(conn):
    migrate(conn)
