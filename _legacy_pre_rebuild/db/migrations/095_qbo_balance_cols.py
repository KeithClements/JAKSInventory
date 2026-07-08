"""Migration 095: QBO bidirectional-sync columns.

Adds columns needed to pull customer / vendor records from QBO and
keep balances up to date locally:

    customers
      + balance              REAL DEFAULT 0      -- QBO open A/R balance
      + qbo_last_pulled_at   TIMESTAMP           -- last successful import

    vendors
      + qbo_last_pulled_at   TIMESTAMP           -- last successful import
      + balance              REAL DEFAULT 0      -- QBO open A/P balance
      + city                 TEXT                -- split BillAddr.City
      + state                TEXT                -- split BillAddr.CountrySubDivisionCode
      + zip                  TEXT                -- split BillAddr.PostalCode

Vendors already have ``name, code, contact_*, address, payment_terms,
notes, qbo_vendor_id`` from the base schema; we only add fields that
the QBO importer needs.

All adds use ``IF NOT EXISTS`` semantics via try/except so the
migration is safe to re-run.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_CUSTOMER_ADDS = [
    ("balance", "REAL DEFAULT 0"),
    ("qbo_last_pulled_at", "TIMESTAMP"),
]

_VENDOR_ADDS = [
    ("qbo_last_pulled_at", "TIMESTAMP"),
    ("balance", "REAL DEFAULT 0"),
    ("city", "TEXT"),
    ("state", "TEXT"),
    ("zip", "TEXT"),
]


def _existing_cols(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return set()
    out = set()
    for r in rows:
        # support both tuple and dict-row shapes
        name = r[1] if not hasattr(r, "keys") else r["name"]
        if name:
            out.add(name)
    return out


def _add_column(conn, table: str, name: str, ddl: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        logger.info("095: added %s.%s", table, name)
    except Exception as exc:
        logger.warning("095: ALTER %s ADD %s failed: %s", table, name, exc)


def migrate(conn) -> None:
    try:
        cust_cols = _existing_cols(conn, "customers")
        for name, ddl in _CUSTOMER_ADDS:
            if name not in cust_cols:
                _add_column(conn, "customers", name, ddl)

        vend_cols = _existing_cols(conn, "vendors")
        for name, ddl in _VENDOR_ADDS:
            if name not in vend_cols:
                _add_column(conn, "vendors", name, ddl)

        try:
            conn.commit()
        except Exception:
            pass
    except Exception as exc:
        logger.warning("095: migrate failed: %s", exc)
