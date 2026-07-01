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
    # Precomputed normalized part numbers for fast search (see search_index.py).
    ("cross_references", "ref_number_norm",        "TEXT NULL"),
    ("product_vendor_sources", "vendor_part_number_norm", "TEXT NULL"),
    ("product_vendor_sources", "vendor_sku_norm",  "TEXT NULL"),

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

    # ── Shopify inventory sync — cache the variant's InventoryItem GID ────────
    ("products", "shopify_inventory_item_id", "VARCHAR(100) NOT NULL DEFAULT ''"),

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

    # ── R3 — statement persistence (coordinated with the statements lane, which
    #    owns app/models/statement.py): over-90 aging bucket + NOT NULL JSON
    #    snapshot on customer_statements. ───────────────────────────────────────
    ("customer_statements", "over_90", "FLOAT NOT NULL DEFAULT 0"),
    ("customer_statements", "snapshot_json", "TEXT NOT NULL DEFAULT ''"),

    # ── R4 — delta-refresh: skip-unchanged staging tally + the Current→Incoming
    #    field diff the preview dock renders for UPDATE candidates. ─────────────
    ("import_batches", "unchanged_count", "INTEGER NOT NULL DEFAULT 0"),
    ("import_candidates", "diff_json", "TEXT NOT NULL DEFAULT ''"),

    # ── PO vendor-confirmed flag — checkbox alongside the free-text conf # so a
    #    vendor's verbal/portal acknowledgement can be recorded without a number ─
    ("purchase_orders", "vendor_confirmed", "BOOLEAN NOT NULL DEFAULT 0"),

    # ── PO bill-to / ship-to (company Locations book + ad-hoc + drop-ship). The
    #    company_locations table itself is created by create_all(); only these PO
    #    columns need an ALTER on existing DBs. ────────────────────────────────
    ("purchase_orders", "bill_to_location_id", "INTEGER NULL"),
    ("purchase_orders", "ship_to_type",        "TEXT NOT NULL DEFAULT 'location'"),
    ("purchase_orders", "ship_to_location_id", "INTEGER NULL"),
    ("purchase_orders", "ship_to_snapshot",    "TEXT NULL"),

    # ── §21 (decision 6.16) — quotes now carry tax so taxable customers aren't
    #    systematically under-quoted. is_taxable defaults TRUE; the create path
    #    overrides it from the customer's is_tax_exempt. ────────────────────────
    ("quotes", "is_taxable",        "BOOLEAN NOT NULL DEFAULT 1"),
    ("quotes", "tax_rate_snapshot", "REAL NOT NULL DEFAULT 0"),

    # ── Lead Finder integration — FMCSA carrier identity (USDOT #) on customers.
    #    The dedup key that ties an ERP customer to a Lead Finder lead's carrier;
    #    one customer per real DOT (see the partial-unique index below). NULL for
    #    every non-carrier customer. ─────────────────────────────────────────────
    ("customers", "usdot_number", "INTEGER NULL"),

    # ── §21 — precomputed normalized SKU for indexed SKU search (also added +
    #    backfilled by search_index.ensure_search_norm_columns on startup). ─────
    ("products", "sku_norm", "TEXT NULL"),

    # ── §21 — persisted QBO vendor binding (qbo_service writes it on first push).
    ("vendors", "qbo_vendor_id", "TEXT NULL"),

    # ── Audit fix sprint — C1: SO carries the tax intent agreed at SO time so
    #    fulfill_and_invoice no longer re-derives tax from the live customer. ───
    ("sales_orders", "is_taxable",        "BOOLEAN NOT NULL DEFAULT 0"),
    ("sales_orders", "tax_rate_snapshot", "REAL NOT NULL DEFAULT 0"),

    # ── Audit fix sprint — C5 + Q6: warranty claim line serial/ESN capture
    #    (makes print template's line.serial_number real) + labor reimbursement
    #    (hours × rate), entered during the warranty process. ──────────────────
    ("warranty_claim_lines", "serial_number", "TEXT NULL"),
    ("warranty_claim_lines", "labor_hours",   "REAL NOT NULL DEFAULT 0"),
    ("warranty_claim_lines", "labor_rate",    "REAL NOT NULL DEFAULT 0"),

    # ── Private-label vendors (DFT/Migao, …): mirror the Alembic 0002 column here
    #    too, so a swallowed Alembic failure can't leave this ORM-required column
    #    absent (Vendor.private_label is NOT NULL → every Vendor query would 500). ─
    ("vendors", "private_label", "BOOLEAN NOT NULL DEFAULT 0"),

    # ── Vendor volume discount (PAI 5% over $5k, …): mirror the Alembic 0004
    #    columns here too. volume_discount_pct snapshots the % applied to a PO;
    #    list_unit_cost holds each line's pre-discount price (reversible). The
    #    rule itself reuses the existing vendor_programs table (no new column). ─
    ("purchase_orders", "volume_discount_pct", "REAL NOT NULL DEFAULT 0"),
    ("po_lines",        "list_unit_cost",      "REAL NULL"),

    # ── Vendor availability sync (scraper feed): mirror the Alembic 0005 columns
    #    here too, so a swallowed Alembic failure can't leave Product.vendor_availability
    #    absent (it's NOT NULL → every Product query would 500). Per-vendor status
    #    on the source; denormalized worst-case roll-up on the product. ──────────
    ("product_vendor_sources", "availability_status",     "TEXT NULL"),
    ("product_vendor_sources", "availability_updated_at", "DATETIME NULL"),
    ("products",               "vendor_availability",     "TEXT NOT NULL DEFAULT ''"),

    # ── Availability → live storefront reconcile (Alembic 0006): mirror the
    #    "ERP hid this listing" flag here too. NOT NULL → a missing column would
    #    500 every Product query; the flag gates the safe auto-re-list direction. ─
    ("products",               "shopify_hidden_by_erp",   "BOOLEAN NOT NULL DEFAULT 0"),

    # ── Vendor-billed freight on the bill (Alembic 0007): the vendor (e.g. PAI)
    #    charges freight on the same invoice as the parts. Part of total_amount so
    #    the AP payable matches the vendor's invoice; defaults from PO freight_in.
    #    NOT NULL → a missing column would 500 every VendorBill query. ────────────
    ("vendor_bills",           "freight_amount",          "REAL NOT NULL DEFAULT 0"),

    # ── §23.3 Phase 1 #2 — precomputed normalized competitor part number for
    #    indexed search (also added + backfilled by
    #    search_index.ensure_search_norm_columns on startup). Mirrors sku_norm. ──
    ("competitor_prices",      "competitor_part_number_norm", "TEXT NULL"),
]


def _backfill_customer_status(conn) -> None:
    """Set customer_status='inactive' where is_active=0 (backfill after ALTER)."""
    conn.execute(text(
        "UPDATE customers SET customer_status='inactive' WHERE is_active=0"
        " AND customer_status='active'"
    ))


def _apply_inline_migrations(bind=None) -> None:
    """Run idempotent ALTER TABLE ADD COLUMN for any columns missing from
    existing databases. New databases pick everything up from create_all().

    ``bind`` lets tests run this against an isolated legacy engine; defaults
    to the module-level live engine."""
    target = bind if bind is not None else engine
    inspector = inspect(target)
    existing_tables = set(inspector.get_table_names())

    # Determine the actual work first (columns genuinely missing) so we only back
    # up + ALTER when there's a real schema change to apply.
    pending: list[tuple[str, str, str]] = []
    for table, column, sql_def in _PENDING_COLUMN_ADDITIONS:
        if table not in existing_tables:
            continue  # fresh DB — create_all() handled it
        cols = {c["name"] for c in inspector.get_columns(table)}
        if column not in cols:
            pending.append((table, column, sql_def))

    # §21 — snapshot the live SQLite file BEFORE mutating its schema, so a bad
    # ALTER can't leave the only copy of the data broken. Only for the live file
    # engine (bind is None) and only when a migration will actually run.
    if pending and bind is None:
        _backup_before_migration(len(pending))

    _customer_status_added = False
    with target.begin() as conn:
        for table, column, sql_def in pending:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sql_def}'))
            log.info("Added column %s.%s", table, column)
            if table == "customers" and column == "customer_status":
                _customer_status_added = True
    # Backfill customer_status for deactivated rows (runs once, after the ALTER)
    if _customer_status_added:
        with target.begin() as conn:
            _backfill_customer_status(conn)


def _backup_before_migration(n_pending: int) -> None:
    """§21 — best-effort copy of the live SQLite file to ``backups/`` before a
    schema migration runs. Never blocks startup; skipped for in-memory DBs."""
    try:
        if ":memory:" in str(engine.url) or not DB_PATH.exists():
            return
        import shutil
        from datetime import datetime
        backup_dir = DB_PATH.parent.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"{DB_PATH.stem}-premigration-{stamp}.db"
        shutil.copy2(DB_PATH, dest)
        log.info("Pre-migration backup (%d pending column(s)) → %s", n_pending, dest)
    except Exception:
        log.exception("pre-migration backup failed (continuing migration)")


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
    # §21 — search's "last sold price" lookup + sales-by-product report scan
    # InvoiceLine by product_id; unindexed it was a full scan that grows with
    # invoice history.
    ("ix_invoice_lines_product_id", "invoice_lines", "product_id"),
    # §21 — engine-application search joins product_applications by product_id.
    ("ix_product_applications_product_id", "product_applications", "product_id"),
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


# ── R3 — DB-level uniqueness backstops ────────────────────────────────────────
# Until now every dedup rule lived in app code only (service-level checks); a
# bug or an ORM-bypassing write could silently create duplicate active vendor
# sources, cross-references, customer account numbers, or vendor bills. These
# unique indexes are the database-level backstop. SQLite partial indexes
# (WHERE ...) let history / blank rows repeat legitimately.
#
# DEFENSIVE CREATION: a live DB may already contain duplicates (the 13k-part PAI
# import is known to have produced duplicate cross_references). Before creating
# each index we run a cheap duplicate-count probe; if duplicates exist we SKIP
# creation and log ONE clear WARNING naming the table + duplicate-group count —
# the owner cleans the data up first, we NEVER delete rows here. Each probe +
# create also runs in its OWN transaction inside try/except so nothing about
# this batch can wedge startup. An integrity backstop must never brick the app.
#
# Each entry is mirrored as a unique Index in the owning model's __table_args__
# (sqlite_where for the partial ones) so fresh databases and in-memory test
# engines get the constraint from create_all(). The index NAMES here must match
# the model definitions exactly so the two creation paths are no-ops against
# each other. Append-only, same discipline as the column migrations above.
#
# Tuple shape: (index_name, table, duplicate_probe_sql, create_sql, dedup_hint).
# probe_sql returns the number of DUPLICATE GROUPS (key tuples with >1 row)
# under exactly the same WHERE scope as the partial index.
_PENDING_UNIQUE_INDEXES: list[tuple[str, str, str, str, str]] = [
    (
        "uq_pvs_product_vendor_active",
        "product_vendor_sources",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM product_vendor_sources WHERE is_active = 1 "
        "GROUP BY product_id, vendor_id HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pvs_product_vendor_active "
        "ON product_vendor_sources (product_id, vendor_id) "
        "WHERE is_active = 1",
        "multiple ACTIVE vendor sources exist for the same (product_id, "
        "vendor_id) — deactivate or merge the duplicate sources",
    ),
    (
        "uq_cross_references_product_type_number",
        "cross_references",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM cross_references "
        "GROUP BY product_id, ref_type, ref_number HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cross_references_product_type_number "
        "ON cross_references (product_id, ref_type, ref_number)",
        "duplicate (product_id, ref_type, ref_number) rows exist — delete the "
        "extra cross-reference rows",
    ),
    (
        "uq_customers_account_number",
        "customers",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM customers "
        "WHERE account_number IS NOT NULL AND account_number != '' "
        "GROUP BY account_number HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_account_number "
        "ON customers (account_number) "
        "WHERE account_number IS NOT NULL AND account_number != ''",
        "multiple customers share the same non-blank account_number — blank out "
        "or renumber the duplicates",
    ),
    (
        "uq_vendor_bills_vendor_bill_number",
        "vendor_bills",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM vendor_bills "
        "WHERE bill_number IS NOT NULL AND bill_number != '' "
        "GROUP BY vendor_id, bill_number HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vendor_bills_vendor_bill_number "
        "ON vendor_bills (vendor_id, bill_number) "
        "WHERE bill_number IS NOT NULL AND bill_number != ''",
        "the same vendor has two bills with the same bill_number — void or "
        "renumber the duplicate bill",
    ),
    (
        "uq_customers_usdot_number",
        "customers",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM customers "
        "WHERE usdot_number IS NOT NULL "
        "GROUP BY usdot_number HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_usdot_number "
        "ON customers (usdot_number) "
        "WHERE usdot_number IS NOT NULL",
        "multiple customers share the same USDOT number — merge the duplicate "
        "carrier records before the Lead Finder dedup backstop can be created",
    ),
    (
        # Risk #2 (revision 0008) — no two ACTIVE vendors share a name. Partial:
        # deactivated vendors' names repeat freely. If live data already has
        # dupes, this SKIPS with a warning so startup never wedges; the owner
        # merges/deactivates, then it creates next boot. Name must match
        # Vendor.__table_args__ (app/models/vendor.py).
        "uq_vendors_name_active",
        "vendors",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM vendors WHERE is_active = 1 "
        "GROUP BY name HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vendors_name_active "
        "ON vendors (name) "
        "WHERE is_active = 1",
        "multiple ACTIVE vendors share the same name — deactivate or merge the "
        "duplicate vendor records",
    ),
    (
        # Risk #2 (revision 0008) — a non-blank vendor_code is globally unique
        # (it drives the internal vendor_sku). Partial: the '' default repeats
        # freely. Name must match Vendor.__table_args__ (app/models/vendor.py).
        "uq_vendors_vendor_code",
        "vendors",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM vendors WHERE vendor_code != '' "
        "GROUP BY vendor_code HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vendors_vendor_code "
        "ON vendors (vendor_code) "
        "WHERE vendor_code != ''",
        "multiple vendors share the same non-blank vendor_code — blank out or "
        "renumber the duplicates",
    ),
    (
        # C10 — one customer per real email (case-insensitive). Partial: blank
        # emails repeat freely. If live data already has dupes, this SKIPS with a
        # warning so startup never wedges; the owner merges, then it creates next boot.
        "uq_customers_email",
        "customers",
        "SELECT COUNT(*) FROM ("
        "SELECT 1 FROM customers "
        "WHERE email IS NOT NULL AND email != '' "
        "GROUP BY lower(email) HAVING COUNT(*) > 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_email "
        "ON customers (email COLLATE NOCASE) "
        "WHERE email IS NOT NULL AND email != ''",
        "multiple customers share the same email — merge the duplicate customer "
        "records before the email dedup backstop can be created",
    ),
]


def _apply_unique_index_migrations(bind=None) -> None:
    """Create the R3 unique-index backstops on the live DB. Defensive, per index:

      1. PROBE: count duplicate key-groups under the index's scope. If any
         exist, SKIP creation and log a WARNING naming the table + count —
         the owner dedups first; we never delete data here.
      2. CREATE: ``CREATE UNIQUE INDEX IF NOT EXISTS`` in its own transaction.

    Everything is wrapped in try/except per index so a failure (race with a
    concurrent write, unexpected schema state) logs which index failed and
    moves on — this batch must never wedge startup.

    ``bind`` lets tests run this against an isolated engine; defaults to the
    module-level live engine.
    """
    target = bind if bind is not None else engine
    inspector = inspect(target)
    existing_tables = set(inspector.get_table_names())
    for idx_name, table, probe_sql, create_sql, dedup_hint in _PENDING_UNIQUE_INDEXES:
        if table not in existing_tables:
            continue
        try:
            with target.begin() as conn:
                dup_groups = conn.execute(text(probe_sql)).scalar() or 0
            if dup_groups:
                log.warning(
                    "Unique index %s SKIPPED — table %s has %d duplicate "
                    "key-group(s); dedup needed before the backstop can be "
                    "created: %s",
                    idx_name, table, dup_groups, dedup_hint,
                )
                continue
            with target.begin() as conn:
                conn.execute(text(create_sql))
        except Exception as exc:  # noqa: BLE001 — backstop must never brick startup
            log.warning(
                "Unique index %s on %s NOT created (%s) — dedup needed: %s",
                idx_name, table, exc, dedup_hint,
            )


def init_db() -> None:
    # Importing __all_models__ is not dead code — the import side-effect registers
    # every model class with Base.metadata so create_all() can see all tables.
    from app.models import __all_models__  # noqa: F401
    # Detect a brand-new DB BEFORE create_all so the Alembic adopter can decide
    # whether to stamp at HEAD (fresh — create_all builds the full current schema)
    # or at BASELINE (an existing pre-Alembic DB that future revisions must still
    # upgrade). 'users' is always present in any initialized DB.
    try:
        _was_fresh = "users" not in set(inspect(engine).get_table_names())
    except Exception:
        _was_fresh = False
    Base.metadata.create_all(bind=engine)
    _apply_inline_migrations()
    _apply_index_migrations()
    _apply_unique_index_migrations()
    # Adopt Alembic / apply any pending revisions. File DBs only — in-memory test
    # DBs build their schema from create_all above and skip Alembic (see
    # app/db_migrate.py). Best-effort: never blocks startup.
    try:
        from app import db_migrate
        db_migrate.adopt(was_fresh=_was_fresh)
    except Exception:
        log.exception("Alembic adoption step errored (continuing)")
