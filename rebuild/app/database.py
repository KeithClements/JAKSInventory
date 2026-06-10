from __future__ import annotations

import logging
from pathlib import Path
from sqlalchemy import create_engine, event, inspect, text
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


# ── SQLite hardening ──────────────────────────────────────────────────────────
# SQLite ships with foreign-key enforcement OFF on every new connection. Without
# this listener, every ForeignKey in the schema is documentation-only — a delete
# or an ORM-bypassing write can silently orphan invoice lines, PO lines, core
# charges, etc. Turn it ON for each pooled connection at connect time.
@event.listens_for(engine, "connect")
def _sqlite_enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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

    # ── Quote ESN/engine header fields (regression: were never on Quote, only ──
    #    on SO/Invoice; carried forward quote→SO→invoice). ──────────────────────
    ("quotes", "customer_po_number",  "VARCHAR(100) NULL"),
    ("quotes", "customer_job_number", "VARCHAR(100) NULL"),
    ("quotes", "esn",                 "VARCHAR(100) NULL"),
    ("quotes", "engine_manufacturer", "VARCHAR(200) NULL"),
    ("quotes", "engine_model",        "VARCHAR(200) NULL"),

    # ── O6 — per-customer CC surcharge override (NULL = use system setting) ───
    ("customers", "card_surcharge_pct", "REAL NULL"),

    # ── Activity Log extensions (ACTIVITY_LOG_CONTRACT.md §1) ────────────────
    # Extend customer_call_logs in place — table name unchanged, 5 new columns.
    ("customer_call_logs", "activity_type",       "TEXT NOT NULL DEFAULT 'call'"),
    ("customer_call_logs", "follow_up_date",      "DATE NULL"),
    ("customer_call_logs", "follow_up_done_at",   "DATETIME NULL"),
    ("customer_call_logs", "related_entity_type", "TEXT NULL"),
    ("customer_call_logs", "related_entity_id",   "INTEGER NULL"),

    # ── Phase 2 — Customer Type (P2-D6) + structured Flags (P2-D2) ───────────
    # customer_type_defaults is a brand-new table → create_all() handles it; only
    # the two new columns on the existing customers table need a backfill here.
    ("customers", "customer_type", "TEXT NOT NULL DEFAULT 'other'"),
    ("customers", "flags",         "VARCHAR(255) NOT NULL DEFAULT ''"),

    # ── Phase 2 §5.2 — SO line ETA (backorder / on-PO arrival estimate) ──────
    ("so_lines", "eta_date", "DATE NULL"),

    # ── Phase 2 #5 — customer account number (external/legacy AR code) ───────
    ("customers", "account_number", "VARCHAR(50) NOT NULL DEFAULT ''"),

    # ── Pricing grid — MarkupTier is a brand-new table (create_all handles it).
    # No ALTER needed; markup_tiers_active + company_website are seeded as
    # Setting rows at startup (see seed_settings in settings.py).

    # ── Customer lifecycle status (4-state enum, owner-locked) ───────────────
    # Default 'active' for new rows; backfill below maps is_active=0 → 'inactive'.
    ("customers", "customer_status", "VARCHAR(20) NOT NULL DEFAULT 'active'"),

    # ── Phase 2 — Product schema v2 (PAI scraper import + market pricing) ─────
    # competitor_prices / competitor_price_history are NEW tables → create_all()
    # makes them; only these new `products` columns need an ALTER backfill.
    # Rule: vendor_cost stays on ProductVendorSource; cost stays moving-avg;
    # margin is computed, never stored. (See Product schema-v2 interview.)
    ("products", "manufacturer_part_number", "TEXT NOT NULL DEFAULT ''"),
    ("products", "product_family",           "TEXT NOT NULL DEFAULT ''"),
    ("products", "engine_family",            "TEXT NOT NULL DEFAULT ''"),
    ("products", "truck_make",               "TEXT NOT NULL DEFAULT ''"),
    ("products", "application_notes",        "TEXT NOT NULL DEFAULT ''"),
    ("products", "country_of_origin",        "TEXT NOT NULL DEFAULT ''"),
    ("products", "is_imported",              "BOOLEAN NOT NULL DEFAULT 0"),
    ("products", "is_house_brand",           "BOOLEAN NOT NULL DEFAULT 0"),
    ("products", "is_performance_part",      "BOOLEAN NOT NULL DEFAULT 0"),
    ("products", "dimensions",               "TEXT NOT NULL DEFAULT ''"),
    ("products", "list_price",               "REAL NULL"),
    ("products", "map_price",                "REAL NULL"),
    ("products", "compare_at_price",         "REAL NULL"),
    ("products", "price_last_checked_at",    "DATETIME NULL"),
    ("products", "price_changed_at",         "DATETIME NULL"),
    ("products", "shopify_status",           "TEXT NOT NULL DEFAULT ''"),
    ("products", "seo_title",                "TEXT NOT NULL DEFAULT ''"),
    ("products", "seo_description",          "TEXT NOT NULL DEFAULT ''"),
    ("products", "search_keywords",          "TEXT NOT NULL DEFAULT ''"),
    ("products", "last_enriched_at",         "DATETIME NULL"),
    ("products", "enrichment_source",        "TEXT NOT NULL DEFAULT ''"),
    ("products", "needs_review",             "BOOLEAN NOT NULL DEFAULT 0"),

    # ── §18 — Category Maintenance: sort order, default markup, import rules.
    #    brands / manufacturers are NEW tables → create_all() handles them. ──
    ("product_categories", "sort_order",         "INTEGER NOT NULL DEFAULT 0"),
    ("product_categories", "default_markup_pct", "REAL NULL"),
    ("product_categories", "import_keywords",    "TEXT NOT NULL DEFAULT ''"),

    # ── JAKS SKU scheme (owner-locked 2026-06-06): vendor-independent
    #    JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]. `code` = short per-category-node code;
    #    `vendor_number` = 1-digit opaque vendor digit (PAI=9, …); products carry the
    #    FROZEN sku components (engine_code/category_code/part_seq) so the assembled
    #    sku never drifts when a category is renamed/re-coded, and a 2nd vendor's
    #    version of the same part can reuse part_seq with a different digit. ──
    ("product_categories", "code",          "TEXT NOT NULL DEFAULT ''"),
    ("vendors",            "vendor_number", "TEXT NOT NULL DEFAULT ''"),
    ("products",           "engine_code",   "TEXT NOT NULL DEFAULT ''"),
    ("products",           "category_code", "TEXT NOT NULL DEFAULT ''"),
    ("products",           "part_seq",      "INTEGER NULL"),
    # pack_qty + is_reman were added to the Product model without a migration
    # shim → a live "no such column: products.pack_qty" 500 on the dashboard
    # against the existing data/jaks.db (tests passed: they use a fresh
    # create_all DB). Defaults match the model (pack_qty=1, is_reman=False).
    ("products",           "pack_qty",      "INTEGER NOT NULL DEFAULT 1"),
    ("products",           "is_reman",      "BOOLEAN NOT NULL DEFAULT 0"),

    # ── R2 — warranty claim ESN (PAI / Interstate-McBee reject claims without
    #    the engine serial number; column on the claim, ESNLookup stays Phase-3) ─
    ("warranty_claims", "esn", "VARCHAR(100) NOT NULL DEFAULT ''"),
]


def _backfill_customer_status(conn) -> None:
    """Set customer_status='inactive' where is_active=0 (backfill after ALTER)."""
    conn.execute(text(
        "UPDATE customers SET customer_status='inactive' WHERE is_active=0"
        " AND customer_status='active'"
    ))


def _apply_inline_migrations() -> None:
    """Run idempotent ALTER TABLE ADD COLUMN for any columns missing from
    existing databases. New databases pick everything up from create_all()."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    _customer_status_added = False
    with engine.begin() as conn:
        for table, column, sql_def in _PENDING_COLUMN_ADDITIONS:
            if table not in existing_tables:
                continue  # fresh DB — create_all() handled it
            cols = {c["name"] for c in inspector.get_columns(table)}
            if column in cols:
                continue
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sql_def}'))
            log.info("Added column %s.%s", table, column)
            if table == "customers" and column == "customer_status":
                _customer_status_added = True
    # Backfill customer_status for deactivated rows (runs once, after the ALTER)
    if _customer_status_added:
        with engine.begin() as conn:
            _backfill_customer_status(conn)


# ── Hot child-table FK indexes ────────────────────────────────────────────────
# SQLite does NOT auto-index foreign keys, so every invoice / quote / SO / PO
# total computation (a child-row lookup by parent id) was a full table scan. At
# real volume that is counter-stalling latency. Idempotent — CREATE INDEX IF NOT
# EXISTS. Append-only, same discipline as the column migrations above.
_PENDING_INDEXES: list[tuple[str, str, str]] = [
    ("ix_invoice_lines_invoice_id", "invoice_lines", "invoice_id"),
    ("ix_quote_lines_quote_id",     "quote_lines",   "quote_id"),
    ("ix_so_lines_so_id",           "so_lines",      "so_id"),
    ("ix_po_lines_po_id",           "po_lines",      "po_id"),
]


def _apply_index_migrations() -> None:
    """Create any missing hot child-table FK indexes. Idempotent."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for idx_name, table, column in _PENDING_INDEXES:
            if table not in existing_tables:
                continue
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})"
            ))


def init_db() -> None:
    # Importing __all_models__ is not dead code — the import side-effect registers
    # every model class with Base.metadata so create_all() can see all tables.
    from app.models import __all_models__  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _apply_inline_migrations()
    _apply_index_migrations()
