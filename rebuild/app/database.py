from __future__ import annotations

import logging
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jaks.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ── Lightweight inline migrations for SQLite ──────────────────────────────────
# We don't use Alembic — too heavy for a single-user local app. Each entry is
# `(table_name, column_name, column_sql_definition)`. On startup we add any
# missing columns via ALTER TABLE; idempotent (skips columns that already exist).
#
# Lifecycle rule:
#   Pre-Phase-A:  dev DB was dropped and recreated via init_db() + create_all().
#                 That covered every model column at that point. Entries below
#                 are a safety-net for any DB that missed the recreate.
#   Post-Phase-A: when a column is added to a model, ALSO add a tuple here so
#                 any production or staging DB picks it up on next server start.
#
# Never delete entries. Never reorder — append only.
_PENDING_COLUMN_ADDITIONS: list[tuple[str, str, str]] = [

    # ── Phase A — Schema expansion (7f79b9e): customers ──────────────────────
    ("customers", "interest_grace_days",      "INTEGER NOT NULL DEFAULT 10"),
    ("customers", "tax_exempt_cert_expiry",   "DATE NULL"),
    ("customers", "tax_exempt_notes",         "TEXT NULL"),
    ("customers", "communication_notes",      "TEXT NOT NULL DEFAULT ''"),
    ("customers", "preferred_contact_method", "TEXT NOT NULL DEFAULT 'phone'"),
    ("customers", "allow_sms",                "BOOLEAN NOT NULL DEFAULT 0"),
    ("customers", "allow_email",              "BOOLEAN NOT NULL DEFAULT 1"),
    ("customers", "allow_marketing",          "BOOLEAN NOT NULL DEFAULT 0"),
    ("customers", "do_not_contact",           "BOOLEAN NOT NULL DEFAULT 0"),
    ("customers", "sms_consent_at",           "DATETIME NULL"),
    ("customers", "sms_consent_method",       "TEXT NULL"),
    ("customers", "email_consent_at",         "DATETIME NULL"),
    ("customers", "opt_out_at",               "DATETIME NULL"),

    # ── Phase A — customer_addresses ─────────────────────────────────────────
    ("customer_addresses", "is_default_shipping", "BOOLEAN NOT NULL DEFAULT 0"),
    ("customer_addresses", "is_default_billing",  "BOOLEAN NOT NULL DEFAULT 0"),
    ("customer_addresses", "street_line2",         "TEXT NOT NULL DEFAULT ''"),
    ("customer_addresses", "contact_name",         "TEXT NOT NULL DEFAULT ''"),
    ("customer_addresses", "phone",                "TEXT NOT NULL DEFAULT ''"),
    ("customer_addresses", "is_active",            "BOOLEAN NOT NULL DEFAULT 1"),

    # ── Phase A — invoices ────────────────────────────────────────────────────
    ("invoices", "tax_rate_snapshot",   "REAL NOT NULL DEFAULT 0"),
    ("invoices", "tax_exempt_snapshot", "BOOLEAN NOT NULL DEFAULT 0"),
    ("invoices", "tax_jurisdiction",    "TEXT NULL"),
    ("invoices", "ship_to_address_id",  "INTEGER NULL"),
    ("invoices", "ship_to_snapshot",    "TEXT NULL"),

    # ── Phase A — invoice_lines ───────────────────────────────────────────────
    ("invoice_lines", "discount_overridden", "BOOLEAN NOT NULL DEFAULT 0"),
    ("invoice_lines", "is_taxable",          "BOOLEAN NOT NULL DEFAULT 1"),
    ("invoice_lines", "tax_amount",          "REAL NOT NULL DEFAULT 0"),

    # ── Phase A — payments ────────────────────────────────────────────────────
    ("payments", "vendor_id",        "INTEGER NULL"),
    ("payments", "sales_order_id",   "INTEGER NULL"),
    ("payments", "direction",        "TEXT NOT NULL DEFAULT 'incoming_from_customer'"),
    ("payments", "surcharge_amount", "REAL NOT NULL DEFAULT 0"),

    # ── Phase A — products ────────────────────────────────────────────────────
    ("products", "last_cost",       "REAL NOT NULL DEFAULT 0"),
    ("products", "qty_backordered", "INTEGER NOT NULL DEFAULT 0"),

    # ── Phase A — cross_references ────────────────────────────────────────────
    ("cross_references", "successful_sale_count",  "INTEGER NOT NULL DEFAULT 0"),
    ("cross_references", "replacement_product_id", "INTEGER NULL"),

    # ── Phase A — po_lines ────────────────────────────────────────────────────
    ("po_lines", "over_received",     "BOOLEAN NOT NULL DEFAULT 0"),
    ("po_lines", "over_received_qty", "INTEGER NOT NULL DEFAULT 0"),
    ("po_lines", "qty_cancelled",     "INTEGER NOT NULL DEFAULT 0"),
    ("po_lines", "cancel_reason",     "TEXT NULL"),
    ("po_lines", "cancelled_at",      "DATETIME NULL"),
    ("po_lines", "cancelled_by_id",   "INTEGER NULL"),

    # ── Phase A — quotes ──────────────────────────────────────────────────────
    ("quotes", "is_duplicate_of_quote_id", "INTEGER NULL"),

    # ── Phase A — so_lines ────────────────────────────────────────────────────
    ("so_lines", "fulfillment_source", "TEXT NOT NULL DEFAULT 'stock'"),
    ("so_lines", "line_status",        "TEXT NOT NULL DEFAULT 'stock'"),
    ("so_lines", "linked_po_line_id",  "INTEGER NULL"),
    ("so_lines", "qty_cancelled",      "INTEGER NOT NULL DEFAULT 0"),
    ("so_lines", "cancel_reason",      "TEXT NULL"),
    ("so_lines", "cancelled_at",       "DATETIME NULL"),
    ("so_lines", "cancelled_by_id",    "INTEGER NULL"),

    # ── Phase A — warranty_claims ─────────────────────────────────────────────
    ("warranty_claims", "warranty_type", "TEXT NOT NULL DEFAULT 'vendor'"),

    # ── Phase A — audit_log ───────────────────────────────────────────────────
    ("audit_log", "field_name", "TEXT NULL"),
    ("audit_log", "old_value",  "TEXT NULL"),
    ("audit_log", "new_value",  "TEXT NULL"),

    # ── Phase A — core_charges ────────────────────────────────────────────────
    ("core_charges", "grace_days_snapshot", "INTEGER NOT NULL DEFAULT 45"),
    ("core_charges", "location_id",         "INTEGER NULL"),

    # ── Phase A — Transaction Workspace: parent/core cascade flags ────────────
    ("invoice_lines", "is_auto_generated",   "BOOLEAN NOT NULL DEFAULT 0"),
    ("invoice_lines", "is_locked_to_parent", "BOOLEAN NOT NULL DEFAULT 0"),
    ("quote_lines",   "is_auto_generated",   "BOOLEAN NOT NULL DEFAULT 0"),
    ("quote_lines",   "is_locked_to_parent", "BOOLEAN NOT NULL DEFAULT 0"),
    ("so_lines",      "parent_line_id",      "INTEGER NULL REFERENCES so_lines(id)"),
    ("so_lines",      "is_core_line",        "BOOLEAN NOT NULL DEFAULT 0"),
    ("so_lines",      "is_auto_generated",   "BOOLEAN NOT NULL DEFAULT 0"),
    ("so_lines",      "is_locked_to_parent", "BOOLEAN NOT NULL DEFAULT 0"),

    # ── Pricing tier & credit limit ───────────────────────────────────────────
    ("customers", "credit_limit",  "REAL NOT NULL DEFAULT 0.0"),
    ("customers", "pricing_tier",  "TEXT NOT NULL DEFAULT 'standard'"),

    # ── Workflow Series 3 — auto-discount override on quote lines ─────────────
    ("quote_lines", "discount_overridden", "BOOLEAN NOT NULL DEFAULT 0"),

    # ── sales_orders QBO sync block (QBOSyncMixin + qbo_so_id) ────────────────
    # Missing on pre-existing DBs; their absence 500s GET /sales-orders/.
    ("sales_orders", "qbo_sync_status",      "TEXT NOT NULL DEFAULT 'pending'"),
    ("sales_orders", "qbo_last_synced_at",   "DATETIME NULL"),
    ("sales_orders", "qbo_sync_error",       "TEXT NULL"),
    ("sales_orders", "qbo_sync_retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("sales_orders", "qbo_so_id",            "TEXT NULL"),

    # ── BUG-4 — core double-credit guard (idempotency stamp) ──────────────────
    ("core_charges", "credit_issued_at",     "DATETIME NULL"),
]


def _apply_inline_migrations() -> None:
    """Run idempotent ALTER TABLE ADD COLUMN for any columns missing from
    existing databases. New databases pick everything up from create_all()."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, sql_def in _PENDING_COLUMN_ADDITIONS:
            if table not in existing_tables:
                continue  # fresh DB — create_all() handled it
            cols = {c["name"] for c in inspector.get_columns(table)}
            if column in cols:
                continue
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sql_def}'))
            log.info("Added column %s.%s", table, column)


def init_db() -> None:
    # Importing __all_models__ is not dead code — the import side-effect registers
    # every model class with Base.metadata so create_all() can see all tables.
    from app.models import __all_models__  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _apply_inline_migrations()
