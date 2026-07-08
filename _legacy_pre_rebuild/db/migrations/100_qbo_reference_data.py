"""Migration 100: QBO reference-data catalog tables (Phase D).

Caches three QBO reference catalogs so the UI can render dropdowns
without hitting the live API on every screen, and so push code can
validate ``class_id``/``location_id``/``qbo_tax_code`` values before
sending a transaction.

Tables (all additive, safe to re-run):
    qbo_tax_codes    — QBO TaxCode (sales tax rates + components)
    qbo_classes      — QBO Class (cost-center / department tagging)
    qbo_locations    — QBO Location/Department (sub-entity tagging)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_TAX_CODES_DDL = """
CREATE TABLE IF NOT EXISTS qbo_tax_codes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    qbo_id            TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    description       TEXT,
    taxable           INTEGER NOT NULL DEFAULT 1,
    rate              REAL,                 -- effective combined rate (pct)
    active            INTEGER NOT NULL DEFAULT 1,
    qbo_pulled_at     TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CLASSES_DDL = """
CREATE TABLE IF NOT EXISTS qbo_classes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    qbo_id            TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    fully_qualified_name TEXT,
    parent_qbo_id     TEXT,
    active            INTEGER NOT NULL DEFAULT 1,
    qbo_pulled_at     TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_LOCATIONS_DDL = """
CREATE TABLE IF NOT EXISTS qbo_locations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    qbo_id            TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    fully_qualified_name TEXT,
    parent_qbo_id     TEXT,
    active            INTEGER NOT NULL DEFAULT 1,
    qbo_pulled_at     TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_qbo_tax_codes_name ON qbo_tax_codes(name)",
    "CREATE INDEX IF NOT EXISTS ix_qbo_tax_codes_active ON qbo_tax_codes(active)",
    "CREATE INDEX IF NOT EXISTS ix_qbo_classes_name ON qbo_classes(name)",
    "CREATE INDEX IF NOT EXISTS ix_qbo_classes_active ON qbo_classes(active)",
    "CREATE INDEX IF NOT EXISTS ix_qbo_locations_name ON qbo_locations(name)",
    "CREATE INDEX IF NOT EXISTS ix_qbo_locations_active ON qbo_locations(active)",
]


def migrate(conn) -> None:
    for ddl in (_TAX_CODES_DDL, _CLASSES_DDL, _LOCATIONS_DDL):
        try:
            conn.execute(ddl)
        except Exception as exc:
            logger.warning("100: create failed: %s", exc)
    for idx in _INDEXES:
        try:
            conn.execute(idx)
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass
    logger.info(
        "Migration 100 complete: qbo_tax_codes, qbo_classes, qbo_locations"
    )
