"""Database schema for JAKS Inventory.

Supports both SQLite and PostgreSQL with compatible syntax.
"""

# SQLite schema (uses AUTOINCREMENT, BOOLEAN)
SQLITE_SCHEMA = """
-- Core products table
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT UNIQUE NOT NULL,
    handle TEXT,
    url TEXT,
    title TEXT,
    category TEXT,
    manufacturer TEXT,
    supplier TEXT,
    engine TEXT,
    condition TEXT,
    
    -- Pricing
    pai_cost REAL DEFAULT 0,
    selling_price REAL DEFAULT 0,
    compare_at_price REAL DEFAULT 0,
    core_charge REAL DEFAULT 0,
    map_price REAL DEFAULT 0,
    
    -- PAI enrichment
    pai_sku TEXT,
    pai_status TEXT,
    pai_stock TEXT,
    pai_link TEXT,
    
    -- Content
    description_html TEXT,
    kit_contents TEXT,
    
    -- Metadata
    image_count INTEGER DEFAULT 0,
    qty_on_hand INTEGER DEFAULT 0,
    stage INTEGER DEFAULT 1,
    shopify_id INTEGER,
    shopify_variant_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scraped_at TIMESTAMP,
    uploaded_at TIMESTAMP,
    
    -- Shopify sync tracking
    shopify_synced_at TIMESTAMP,
    needs_shopify_update BOOLEAN DEFAULT FALSE,
    shopify_inventory_item_id INTEGER,
    shopify_status TEXT DEFAULT 'draft',
    shopify_published_at TIMESTAMP,
    local_modified_at TIMESTAMP,
    reorder_point INTEGER DEFAULT 0,
    hhp_last_price_check TIMESTAMP,
    hhp_price_history TEXT,
    
    -- Min/Max Restock (Phase 2)
    min_qty INTEGER DEFAULT 0,
    max_qty INTEGER DEFAULT 0,
    preferred_vendor_id INTEGER REFERENCES vendors(id),

    -- Product type: inventoried | non_inventoried | service
    product_type TEXT NOT NULL DEFAULT 'inventoried'
);

CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
CREATE INDEX IF NOT EXISTS idx_products_handle ON products(handle);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_manufacturer ON products(manufacturer);
CREATE INDEX IF NOT EXISTS idx_products_pai_sku ON products(pai_sku);

-- Part numbers table (all variants for a product)
CREATE TABLE IF NOT EXISTS part_numbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    number TEXT NOT NULL,
    number_normalized TEXT NOT NULL,
    type TEXT,
    manufacturer TEXT,
    is_primary BOOLEAN DEFAULT 0,
    source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, number_normalized)
);

CREATE INDEX IF NOT EXISTS idx_part_numbers_normalized ON part_numbers(number_normalized);
CREATE INDEX IF NOT EXISTS idx_part_numbers_product ON part_numbers(product_id);
CREATE INDEX IF NOT EXISTS idx_part_numbers_type ON part_numbers(type);

-- Purchase Receipts Table
CREATE TABLE IF NOT EXISTS purchase_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_number TEXT UNIQUE NOT NULL,
    po_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    received_date DATE,
    received_by TEXT,
    vendor_invoice_number TEXT,
    packing_slip_number TEXT,
    freight_amount REAL DEFAULT 0,
    duty_amount REAL DEFAULT 0,
    other_landed_fees REAL DEFAULT 0,
    landed_cost_allocated BOOLEAN DEFAULT 0,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    qbo_bill_id TEXT,
    qbo_sync_status TEXT DEFAULT 'pending',
    qbo_sync_error TEXT,
    finalized_at TIMESTAMP,
    finalized_by TEXT,
    voided_at TIMESTAMP,
    voided_by TEXT,
    void_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_receipt_number ON purchase_receipts(receipt_number);
CREATE INDEX IF NOT EXISTS idx_receipt_po ON purchase_receipts(po_id);
CREATE INDEX IF NOT EXISTS idx_receipt_vendor ON purchase_receipts(vendor_id);
CREATE INDEX IF NOT EXISTS idx_receipt_status ON purchase_receipts(status);

-- Purchase Receipt Lines Table
CREATE TABLE IF NOT EXISTS purchase_receipt_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_id INTEGER NOT NULL REFERENCES purchase_receipts(id) ON DELETE CASCADE,
        po_line_id INTEGER NOT NULL REFERENCES purchase_order_lines(id),
        product_id INTEGER NOT NULL REFERENCES products(id),
        sku TEXT,
        description TEXT,
        qty_received INTEGER NOT NULL DEFAULT 0,
        qty_damaged INTEGER DEFAULT 0,
        qty_backordered INTEGER DEFAULT 0,
        unit_cost REAL DEFAULT 0,
        vendor_core_amount REAL DEFAULT 0,
        landed_unit_cost REAL DEFAULT 0,
        location_id INTEGER REFERENCES storage_locations(id),
        bin_location TEXT,
        serial_required BOOLEAN DEFAULT 0,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_receipt_line_receipt ON purchase_receipt_lines(receipt_id);
    CREATE INDEX IF NOT EXISTS idx_receipt_line_po_line ON purchase_receipt_lines(po_line_id);
    CREATE INDEX IF NOT EXISTS idx_receipt_line_product ON purchase_receipt_lines(product_id);

-- Cross-reference links between part numbers
CREATE TABLE IF NOT EXISTS xref_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_a_id INTEGER NOT NULL REFERENCES part_numbers(id) ON DELETE CASCADE,
    part_b_id INTEGER NOT NULL REFERENCES part_numbers(id) ON DELETE CASCADE,
    source TEXT,
    confidence TEXT DEFAULT 'medium',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(part_a_id, part_b_id)
);

CREATE INDEX IF NOT EXISTS idx_xref_part_a ON xref_links(part_a_id);
CREATE INDEX IF NOT EXISTS idx_xref_part_b ON xref_links(part_b_id);

-- Product images metadata
CREATE TABLE IF NOT EXISTS product_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    position INTEGER DEFAULT 1,
    width INTEGER,
    height INTEGER,
    file_size INTEGER,
    hash TEXT,
    source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(product_id, filename)
);

CREATE INDEX IF NOT EXISTS idx_images_product ON product_images(product_id);
CREATE INDEX IF NOT EXISTS idx_images_hash ON product_images(hash);

-- Inventory transactions (audit trail)
CREATE TABLE IF NOT EXISTS inventory_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    transaction_type TEXT NOT NULL,
    quantity_change INTEGER NOT NULL,
    quantity_before INTEGER NOT NULL,
    quantity_after INTEGER NOT NULL,
    reference TEXT,
    notes TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inv_txn_product ON inventory_transactions(product_id);

-- Vendors table
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT UNIQUE,
    contact_name TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    address TEXT,
    payment_terms TEXT,
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    qbo_vendor_id TEXT,
    lead_time_days INTEGER DEFAULT 14,
    avg_transit_days INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vendors_code ON vendors(code);
CREATE INDEX IF NOT EXISTS idx_vendors_name ON vendors(name);

-- Purchase orders table
CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number TEXT UNIQUE NOT NULL,
    vendor_id INTEGER REFERENCES vendors(id),
    status TEXT DEFAULT 'draft',
    order_date DATE,
    expected_date DATE,
    received_date DATE,
    subtotal REAL DEFAULT 0,
    tax REAL DEFAULT 0,
    shipping REAL DEFAULT 0,
    total REAL DEFAULT 0,
    duty_amount REAL DEFAULT 0,
    other_landed_fees REAL DEFAULT 0,
    freight_allocation_basis TEXT DEFAULT 'value',
    landed_cost_allocated_at TIMESTAMP,
    ship_to TEXT,
    bill_to TEXT,
    notes TEXT,
    vendor_message TEXT,
    drop_ship BOOLEAN DEFAULT FALSE,
    no_tags BOOLEAN DEFAULT FALSE,
    qbo_bill_id TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_po_number ON purchase_orders(po_number);
CREATE INDEX IF NOT EXISTS idx_po_vendor ON purchase_orders(vendor_id);
CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status);

-- Purchase order line items
-- product_id is NULLABLE so manual discount / fee lines can be added
-- without a SKU (line_kind='discount' or 'fee').
CREATE TABLE IF NOT EXISTS purchase_order_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id),
    quantity_ordered INTEGER NOT NULL DEFAULT 0,
    quantity_received INTEGER NOT NULL DEFAULT 0,
    unit_cost REAL DEFAULT 0,
    line_total REAL DEFAULT 0,
    landed_unit_cost REAL,
    landed_allocation REAL,
    discount_amount REAL DEFAULT 0,
    discount_pct REAL DEFAULT 0,
    line_kind TEXT DEFAULT 'product',
    sku TEXT,
    description TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_po_line_po ON purchase_order_lines(po_id);
CREATE INDEX IF NOT EXISTS idx_po_line_product ON purchase_order_lines(product_id);

-- Customers table
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    company TEXT,
    email TEXT,
    phone TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    country TEXT DEFAULT 'USA',
    payment_terms TEXT DEFAULT 'Net 30',
    tax_exempt BOOLEAN DEFAULT FALSE,
    credit_limit REAL DEFAULT 0,
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    qbo_customer_id TEXT,
    shopify_customer_id TEXT,
    -- Profile expansion (migration 033)
    tax_id TEXT DEFAULT '',
    tax_id_image_path TEXT DEFAULT '',
    tax_id_expiration TEXT DEFAULT '',
    birthday TEXT DEFAULT '',
    special_dates TEXT DEFAULT '',
    mobile_phone TEXT DEFAULT '',
    business_phone TEXT DEFAULT '',
    text_permission INTEGER DEFAULT 0,
    delivery_notes TEXT DEFAULT '',
    pricing_tier TEXT DEFAULT 'Standard',
    customer_ranking TEXT DEFAULT '',
    customer_type TEXT DEFAULT '',
    on_hold INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_customers_company ON customers(company);

-- Customer employees (migration 034)
CREATE TABLE IF NOT EXISTS customer_employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    title TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

-- Credit agreements (migration 035)
CREATE TABLE IF NOT EXISTS credit_agreements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    credit_limit REAL DEFAULT 0,
    apr REAL DEFAULT 0,
    net_days INTEGER DEFAULT 30,
    issue_date TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    guarantor_name TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

-- Invoices table
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT UNIQUE NOT NULL,
    customer_id INTEGER REFERENCES customers(id),
    status TEXT DEFAULT 'draft',
    invoice_date DATE,
    due_date DATE,
    ship_date DATE,
    subtotal REAL DEFAULT 0,
    tax_rate REAL DEFAULT 0,
    tax_amount REAL DEFAULT 0,
    shipping REAL DEFAULT 0,
    total REAL DEFAULT 0,
    amount_paid REAL DEFAULT 0,
    balance_due REAL DEFAULT 0,
    payment_method TEXT,
    tracking_number TEXT,
    notes TEXT,
    internal_notes TEXT,
    qbo_invoice_id TEXT,
    shopify_order_id TEXT,
    finalized_at TIMESTAMP,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_invoices_number ON invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date);

-- Invoice line items
CREATE TABLE IF NOT EXISTS invoice_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id),
    serial_id INTEGER REFERENCES serial_numbers(id),
    sku TEXT,
    description TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price REAL DEFAULT 0,
    line_total REAL DEFAULT 0,
    warranty_years INTEGER DEFAULT 0,
    warranty_price REAL DEFAULT 0,
    core_charge REAL DEFAULT 0,
    discount_amount REAL DEFAULT 0,
    discount_pct REAL DEFAULT 0,
    line_kind TEXT DEFAULT 'product',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_invoice_line_invoice ON invoice_lines(invoice_id);
CREATE INDEX IF NOT EXISTS idx_invoice_line_product ON invoice_lines(product_id);
CREATE INDEX IF NOT EXISTS idx_invoice_line_serial ON invoice_lines(serial_id);

-- Storage locations table (Phase I: Bin/Location tracking)
CREATE TABLE IF NOT EXISTS storage_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    aisle TEXT,
    shelf TEXT,
    bin TEXT,
    zone TEXT,
    capacity INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_locations_code ON storage_locations(code);
CREATE INDEX IF NOT EXISTS idx_locations_zone ON storage_locations(zone);

-- Product location mapping (allows product in multiple locations)
CREATE TABLE IF NOT EXISTS product_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    location_id INTEGER NOT NULL REFERENCES storage_locations(id) ON DELETE CASCADE,
    quantity INTEGER DEFAULT 0,
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(product_id, location_id)
);

CREATE INDEX IF NOT EXISTS idx_product_locations_product ON product_locations(product_id);
CREATE INDEX IF NOT EXISTS idx_product_locations_location ON product_locations(location_id);

-- Serial/Lot numbers table (Phase I Part 2: Serial tracking)
CREATE TABLE IF NOT EXISTS serial_numbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    serial_number TEXT NOT NULL,
    lot_number TEXT,
    status TEXT DEFAULT 'available',
    location_id INTEGER REFERENCES storage_locations(id),
    cost REAL DEFAULT 0,
    received_date DATE,
    sold_date DATE,
    expiry_date DATE,
    warranty_expiry DATE,
    invoice_id INTEGER REFERENCES invoices(id),
    po_id INTEGER REFERENCES purchase_orders(id),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(product_id, serial_number)
);

CREATE INDEX IF NOT EXISTS idx_serial_product ON serial_numbers(product_id);
CREATE INDEX IF NOT EXISTS idx_serial_number ON serial_numbers(serial_number);
CREATE INDEX IF NOT EXISTS idx_serial_status ON serial_numbers(status);
CREATE INDEX IF NOT EXISTS idx_serial_lot ON serial_numbers(lot_number);

-- Core returns table (Phase I Part 3: Core tracking)
CREATE TABLE IF NOT EXISTS core_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_line_id INTEGER REFERENCES invoice_lines(id) ON DELETE CASCADE,
    invoice_id INTEGER REFERENCES invoices(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    customer_id INTEGER REFERENCES customers(id),
    core_charge REAL DEFAULT 0,
    customer_core_price REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    due_date DATE,
    received_date DATE,
    credit_issued REAL DEFAULT 0,
    credit_date DATE,
    credit_invoice_id INTEGER REFERENCES invoices(id),
    serial_number TEXT,
    condition TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_core_invoice ON core_returns(invoice_id);
CREATE INDEX IF NOT EXISTS idx_core_customer ON core_returns(customer_id);
CREATE INDEX IF NOT EXISTS idx_core_product ON core_returns(product_id);
CREATE INDEX IF NOT EXISTS idx_core_status ON core_returns(status);

-- Customer credits ledger (migration 069)
CREATE TABLE IF NOT EXISTS customer_credits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    source_type TEXT NOT NULL,
    source_id INTEGER,
    amount REAL NOT NULL DEFAULT 0,
    applied_to_invoice_id INTEGER REFERENCES invoices(id),
    issue_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    applied_date TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'open',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_customer_credits_customer_status
    ON customer_credits(customer_id, status);
CREATE INDEX IF NOT EXISTS ix_customer_credits_source
    ON customer_credits(source_type, source_id);
CREATE INDEX IF NOT EXISTS ix_customer_credits_applied_invoice
    ON customer_credits(applied_to_invoice_id);

-- Lost-sale capture (migration 080 / F1)
CREATE TABLE IF NOT EXISTS lost_sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER REFERENCES customers(id),
    product_id      INTEGER REFERENCES products(id),
    sku_requested   TEXT,
    description     TEXT,
    quantity        INTEGER DEFAULT 1,
    quoted_price    REAL DEFAULT 0,
    reason          TEXT,
    source_quote_id INTEGER,
    notes           TEXT,
    lost_date       DATE DEFAULT CURRENT_DATE,
    created_by      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_lost_sales_customer ON lost_sales(customer_id);
CREATE INDEX IF NOT EXISTS ix_lost_sales_product ON lost_sales(product_id);
CREATE INDEX IF NOT EXISTS ix_lost_sales_reason ON lost_sales(reason);
CREATE INDEX IF NOT EXISTS ix_lost_sales_date ON lost_sales(lost_date);

-- Per-payment history (migration 081 / T1.1)
CREATE TABLE IF NOT EXISTS invoice_payments (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id            INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    amount                REAL NOT NULL DEFAULT 0,
    payment_method        TEXT,
    reference             TEXT,
    notes                 TEXT,
    status                TEXT NOT NULL DEFAULT 'posted',
    overpayment_credit_id INTEGER REFERENCES customer_credits(id),
    recorded_by           TEXT,
    recorded_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    voided_at             TIMESTAMP,
    voided_by             TEXT,
    void_reason           TEXT
);
CREATE INDEX IF NOT EXISTS ix_invoice_payments_invoice ON invoice_payments(invoice_id);
CREATE INDEX IF NOT EXISTS ix_invoice_payments_status  ON invoice_payments(status);
CREATE INDEX IF NOT EXISTS ix_invoice_payments_recorded ON invoice_payments(recorded_at);
CREATE INDEX IF NOT EXISTS ix_invoice_payments_method  ON invoice_payments(payment_method);

-- Product fitments / application matrix (migration 083 / T1.3)
CREATE TABLE IF NOT EXISTS product_fitments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    make          TEXT,
    model         TEXT,
    year_from     INTEGER,
    year_to       INTEGER,
    engine_family TEXT,
    engine_model  TEXT,
    position      TEXT,
    notes         TEXT,
    source        TEXT DEFAULT 'manual',
    confidence    INTEGER,
    created_by    TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_fitments_product ON product_fitments(product_id);
CREATE INDEX IF NOT EXISTS ix_fitments_make    ON product_fitments(make);
CREATE INDEX IF NOT EXISTS ix_fitments_model   ON product_fitments(model);
CREATE INDEX IF NOT EXISTS ix_fitments_engine  ON product_fitments(engine_model);
CREATE INDEX IF NOT EXISTS ix_fitments_year    ON product_fitments(year_from, year_to);

-- Part supersessions / replacement chains (migration 084 / T1.4)
CREATE TABLE IF NOT EXISTS part_supersessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    old_product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    new_product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    direction       TEXT DEFAULT 'replaces',
    effective_date  TEXT,
    reason          TEXT,
    notes           TEXT,
    created_by      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (old_product_id, new_product_id)
);
CREATE INDEX IF NOT EXISTS ix_super_old ON part_supersessions(old_product_id);
CREATE INDEX IF NOT EXISTS ix_super_new ON part_supersessions(new_product_id);

-- Inventory transfers table (Phase I Part 4: Multi-location transfers)
CREATE TABLE IF NOT EXISTS inventory_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_number TEXT UNIQUE NOT NULL,
    from_location_id INTEGER NOT NULL REFERENCES storage_locations(id),
    to_location_id INTEGER NOT NULL REFERENCES storage_locations(id),
    status TEXT DEFAULT 'draft',
    requested_date DATE,
    approved_date DATE,
    completed_date DATE,
    requested_by TEXT,
    approved_by TEXT,
    completed_by TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transfer_number ON inventory_transfers(transfer_number);
CREATE INDEX IF NOT EXISTS idx_transfer_from ON inventory_transfers(from_location_id);
CREATE INDEX IF NOT EXISTS idx_transfer_to ON inventory_transfers(to_location_id);
CREATE INDEX IF NOT EXISTS idx_transfer_status ON inventory_transfers(status);

-- Transfer line items
CREATE TABLE IF NOT EXISTS transfer_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id INTEGER NOT NULL REFERENCES inventory_transfers(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 1,
    serial_id INTEGER REFERENCES serial_numbers(id),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transfer_line_transfer ON transfer_lines(transfer_id);
CREATE INDEX IF NOT EXISTS idx_transfer_line_product ON transfer_lines(product_id);

-- Part Interchanges (Phase 2)
CREATE TABLE IF NOT EXISTS part_interchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    interchange_number TEXT NOT NULL,
    interchange_type TEXT NOT NULL,
    manufacturer TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, interchange_number)
);

CREATE INDEX IF NOT EXISTS idx_interchange_number ON part_interchanges(interchange_number);
CREATE INDEX IF NOT EXISTS idx_interchange_product ON part_interchanges(product_id);

-- Inventory Audits (Phase 1C)
CREATE TABLE IF NOT EXISTS inventory_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER REFERENCES storage_locations(id),
    audit_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    count_type TEXT DEFAULT 'full',
    status TEXT DEFAULT 'in_progress',
    notes TEXT,
    total_items INTEGER DEFAULT 0,
    total_variances INTEGER DEFAULT 0,
    variance_value REAL DEFAULT 0,
    created_by TEXT,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES inventory_audits(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    system_qty INTEGER DEFAULT 0,
    counted_qty INTEGER,
    variance INTEGER DEFAULT 0,
    variance_value REAL DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_lines_audit ON audit_lines(audit_id);
CREATE INDEX IF NOT EXISTS idx_audit_lines_product ON audit_lines(product_id);

-- QBO Export Tracking (Phase 1D)
CREATE TABLE IF NOT EXISTS qbo_exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    export_type TEXT NOT NULL,
    export_format TEXT DEFAULT 'csv',
    record_count INTEGER DEFAULT 0,
    file_path TEXT,
    notes TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ESN Cache (Phase 4)
CREATE TABLE IF NOT EXISTS esn_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    esn TEXT UNIQUE NOT NULL,
    oem TEXT NOT NULL,
    engine_model TEXT,
    horsepower TEXT,
    build_date TEXT,
    cpl TEXT,
    application TEXT,
    emission_level TEXT,
    raw_data TEXT,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_esn_esn ON esn_cache(esn);
CREATE INDEX IF NOT EXISTS idx_esn_oem ON esn_cache(oem);

CREATE TABLE IF NOT EXISTS engine_compatibility (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine_model TEXT NOT NULL,
    category TEXT NOT NULL,
    product_id INTEGER REFERENCES products(id),
    oem_part_number TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_engine_compat_model ON engine_compatibility(engine_model);
CREATE INDEX IF NOT EXISTS idx_engine_compat_product ON engine_compatibility(product_id);

-- Work Orders (Phase 5A)
CREATE TABLE IF NOT EXISTS work_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wo_number TEXT UNIQUE NOT NULL,
    customer_id INTEGER REFERENCES customers(id),
    vehicle_id INTEGER,
    status TEXT DEFAULT 'draft',
    priority TEXT DEFAULT 'normal',
    complaint TEXT,
    diagnosis TEXT,
    technician TEXT,
    estimated_hours REAL DEFAULT 0,
    actual_hours REAL DEFAULT 0,
    labor_rate REAL DEFAULT 0,
    parts_total REAL DEFAULT 0,
    labor_total REAL DEFAULT 0,
    total REAL DEFAULT 0,
    invoice_id INTEGER REFERENCES invoices(id),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    notes TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wo_number ON work_orders(wo_number);
CREATE INDEX IF NOT EXISTS idx_wo_customer ON work_orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_wo_status ON work_orders(status);
CREATE INDEX IF NOT EXISTS idx_wo_vehicle ON work_orders(vehicle_id);

CREATE TABLE IF NOT EXISTS work_order_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wo_id INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    line_type TEXT NOT NULL DEFAULT 'part',
    product_id INTEGER REFERENCES products(id),
    serial_id INTEGER REFERENCES serial_numbers(id),
    description TEXT,
    quantity INTEGER DEFAULT 1,
    unit_price REAL DEFAULT 0,
    line_total REAL DEFAULT 0,
    hours REAL DEFAULT 0,
    technician TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wol_wo ON work_order_lines(wo_id);
CREATE INDEX IF NOT EXISTS idx_wol_product ON work_order_lines(product_id);

-- Vehicles / Fleet Registry (Phase 5B)
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(id),
    unit_number TEXT,
    vin TEXT,
    year INTEGER,
    make TEXT,
    model TEXT,
    engine_make TEXT,
    engine_model TEXT,
    esn TEXT,
    mileage INTEGER DEFAULT 0,
    license_plate TEXT,
    dot_number TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vehicles_customer ON vehicles(customer_id);
CREATE INDEX IF NOT EXISTS idx_vehicles_vin ON vehicles(vin);
CREATE INDEX IF NOT EXISTS idx_vehicles_unit ON vehicles(unit_number);
CREATE INDEX IF NOT EXISTS idx_vehicles_esn ON vehicles(esn);

-- Returns & Warranty (Phase 5E)
CREATE TABLE IF NOT EXISTS returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rma_number TEXT UNIQUE NOT NULL,
    invoice_id INTEGER REFERENCES invoices(id),
    customer_id INTEGER REFERENCES customers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    serial_id INTEGER REFERENCES serial_numbers(id),
    quantity INTEGER DEFAULT 1,
    reason TEXT,
    condition TEXT,
    status TEXT DEFAULT 'pending',
    action_taken TEXT,
    refund_amount REAL DEFAULT 0,
    restocking_fee REAL DEFAULT 0,
    restocking_pct REAL DEFAULT 0,
    net_refund REAL DEFAULT 0,
    vendor_claim_id TEXT,
    vendor_claim_status TEXT,
    warranty_expiry DATE,
    notes TEXT,
    received_date DATE,
    resolved_date DATE,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_returns_rma ON returns(rma_number);
CREATE INDEX IF NOT EXISTS idx_returns_customer ON returns(customer_id);
CREATE INDEX IF NOT EXISTS idx_returns_product ON returns(product_id);
CREATE INDEX IF NOT EXISTS idx_returns_status ON returns(status);

-- Scraper tables
CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    total_searched INTEGER DEFAULT 0,
    total_found INTEGER DEFAULT 0,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS scraped_cross_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    source_part_number TEXT NOT NULL,
    found_alternate_number TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence TEXT DEFAULT 'medium',
    match_type TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    run_id INTEGER REFERENCES scrape_runs(id),
    status TEXT NOT NULL DEFAULT 'pending',
    accepted_at TIMESTAMP,
    UNIQUE(source_part_number, found_alternate_number, source)
);

CREATE INDEX IF NOT EXISTS idx_scraped_xref_status ON scraped_cross_references(status);
CREATE INDEX IF NOT EXISTS idx_scraped_xref_product ON scraped_cross_references(product_id);

CREATE TABLE IF NOT EXISTS competitor_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    sku_searched TEXT NOT NULL,
    competitor_name TEXT NOT NULL,
    competitor_part_number TEXT,
    competitor_price REAL,
    availability TEXT,
    source_url TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    run_id INTEGER REFERENCES scrape_runs(id),
    status TEXT NOT NULL DEFAULT 'pending',
    accepted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_comp_price_status ON competitor_prices(status);
CREATE INDEX IF NOT EXISTS idx_comp_price_product ON competitor_prices(product_id);
"""

# PostgreSQL schema (uses SERIAL, BOOLEAN, different timestamp syntax)
POSTGRESQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    sku TEXT UNIQUE NOT NULL,
    handle TEXT,
    url TEXT,
    title TEXT,
    category TEXT,
    manufacturer TEXT,
    supplier TEXT,
    engine TEXT,
    condition TEXT,
    
    -- Pricing
    cost DECIMAL(10,2) DEFAULT 0,
    pai_cost DECIMAL(10,2) DEFAULT 0,
    selling_price DECIMAL(10,2) DEFAULT 0,
    compare_at_price DECIMAL(10,2) DEFAULT 0,
    core_charge DECIMAL(10,2) DEFAULT 0,
    
    -- PAI enrichment
    pai_sku TEXT,
    pai_status TEXT,
    pai_stock TEXT,
    pai_link TEXT,
    
    -- Content
    description_html TEXT,
    kit_contents TEXT,
    
    -- Metadata
    image_count INTEGER DEFAULT 0,
    qty_on_hand INTEGER DEFAULT 0,
    stage INTEGER DEFAULT 1,
    shopify_id BIGINT,
    shopify_variant_id BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    scraped_at TIMESTAMP,
    uploaded_at TIMESTAMP,
    
    -- Shopify sync tracking
    shopify_synced_at TIMESTAMP,
    needs_shopify_update BOOLEAN DEFAULT FALSE,
    shopify_inventory_item_id BIGINT,
    shopify_status TEXT DEFAULT 'draft',
    shopify_published_at TIMESTAMP,
    local_modified_at TIMESTAMP,
    reorder_point INTEGER DEFAULT 0,
    hhp_last_price_check TIMESTAMP,
    hhp_price_history TEXT,
    
    -- Min/Max Restock (Phase 2)
    min_qty INTEGER DEFAULT 0,
    max_qty INTEGER DEFAULT 0,
    preferred_vendor_id INTEGER REFERENCES vendors(id),

    -- Product type: inventoried | non_inventoried | service
    product_type TEXT NOT NULL DEFAULT 'inventoried'
);
-- Purchase Receipts Table
CREATE TABLE IF NOT EXISTS purchase_receipts (
    id SERIAL PRIMARY KEY,
    receipt_number TEXT UNIQUE NOT NULL,
    po_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    received_date DATE,
    received_by TEXT,
    vendor_invoice_number TEXT,
    packing_slip_number TEXT,
    freight_amount DECIMAL(10,2) DEFAULT 0,
    duty_amount DECIMAL(10,2) DEFAULT 0,
    other_landed_fees DECIMAL(10,2) DEFAULT 0,
    landed_cost_allocated BOOLEAN DEFAULT FALSE,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    qbo_bill_id TEXT,
    qbo_sync_status TEXT DEFAULT 'pending',
    qbo_sync_error TEXT,
    finalized_at TIMESTAMP,
    finalized_by TEXT,
    voided_at TIMESTAMP,
    voided_by TEXT,
    void_reason TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_receipt_number ON purchase_receipts(receipt_number);
CREATE INDEX IF NOT EXISTS idx_receipt_po ON purchase_receipts(po_id);
CREATE INDEX IF NOT EXISTS idx_receipt_vendor ON purchase_receipts(vendor_id);
CREATE INDEX IF NOT EXISTS idx_receipt_status ON purchase_receipts(status);

-- Purchase Receipt Lines Table
CREATE TABLE IF NOT EXISTS purchase_receipt_lines (
    id SERIAL PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES purchase_receipts(id) ON DELETE CASCADE,
    po_line_id INTEGER NOT NULL REFERENCES purchase_order_lines(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    sku TEXT,
    description TEXT,
    qty_received INTEGER NOT NULL DEFAULT 0,
    qty_damaged INTEGER DEFAULT 0,
    qty_backordered INTEGER DEFAULT 0,
    unit_cost DECIMAL(10,2) DEFAULT 0,
    vendor_core_amount DECIMAL(10,2) DEFAULT 0,
    landed_unit_cost DECIMAL(10,2) DEFAULT 0,
    location_id INTEGER REFERENCES storage_locations(id),
    bin_location TEXT,
    serial_required BOOLEAN DEFAULT FALSE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_receipt_line_receipt ON purchase_receipt_lines(receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipt_line_po_line ON purchase_receipt_lines(po_line_id);
CREATE INDEX IF NOT EXISTS idx_receipt_line_product ON purchase_receipt_lines(product_id);
CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
CREATE INDEX IF NOT EXISTS idx_products_handle ON products(handle);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_manufacturer ON products(manufacturer);
CREATE INDEX IF NOT EXISTS idx_products_pai_sku ON products(pai_sku);

-- Part numbers table
CREATE TABLE IF NOT EXISTS part_numbers (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    number TEXT NOT NULL,
    number_normalized TEXT NOT NULL,
    type TEXT,
    manufacturer TEXT,
    is_primary BOOLEAN DEFAULT FALSE,
    source TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(product_id, number_normalized)
);

CREATE INDEX IF NOT EXISTS idx_part_numbers_normalized ON part_numbers(number_normalized);
CREATE INDEX IF NOT EXISTS idx_part_numbers_product ON part_numbers(product_id);
CREATE INDEX IF NOT EXISTS idx_part_numbers_type ON part_numbers(type);

-- Cross-reference links
CREATE TABLE IF NOT EXISTS xref_links (
    id SERIAL PRIMARY KEY,
    part_a_id INTEGER NOT NULL REFERENCES part_numbers(id) ON DELETE CASCADE,
    part_b_id INTEGER NOT NULL REFERENCES part_numbers(id) ON DELETE CASCADE,
    source TEXT,
    confidence TEXT DEFAULT 'medium',
    created_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(part_a_id, part_b_id)
);

CREATE INDEX IF NOT EXISTS idx_xref_part_a ON xref_links(part_a_id);
CREATE INDEX IF NOT EXISTS idx_xref_part_b ON xref_links(part_b_id);

-- Product images
CREATE TABLE IF NOT EXISTS product_images (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    position INTEGER DEFAULT 1,
    width INTEGER,
    height INTEGER,
    file_size INTEGER,
    hash TEXT,
    source TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(product_id, filename)
);

CREATE INDEX IF NOT EXISTS idx_images_product ON product_images(product_id);
CREATE INDEX IF NOT EXISTS idx_images_hash ON product_images(hash);

-- Inventory transactions (audit trail)
CREATE TABLE IF NOT EXISTS inventory_transactions (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    transaction_type TEXT NOT NULL,
    quantity_change INTEGER NOT NULL,
    quantity_before INTEGER NOT NULL,
    quantity_after INTEGER NOT NULL,
    reference TEXT,
    notes TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inv_txn_product ON inventory_transactions(product_id);

-- Vendors table
CREATE TABLE IF NOT EXISTS vendors (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT UNIQUE,
    contact_name TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    address TEXT,
    payment_terms TEXT,
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    qbo_vendor_id TEXT,
    lead_time_days INTEGER DEFAULT 14,
    avg_transit_days INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vendors_code ON vendors(code);
CREATE INDEX IF NOT EXISTS idx_vendors_name ON vendors(name);

-- Purchase orders table
CREATE TABLE IF NOT EXISTS purchase_orders (
    id SERIAL PRIMARY KEY,
    po_number TEXT UNIQUE NOT NULL,
    vendor_id INTEGER REFERENCES vendors(id),
    status TEXT DEFAULT 'draft',
    order_date DATE,
    expected_date DATE,
    received_date DATE,
    subtotal DECIMAL(10,2) DEFAULT 0,
    tax DECIMAL(10,2) DEFAULT 0,
    shipping DECIMAL(10,2) DEFAULT 0,
    total DECIMAL(10,2) DEFAULT 0,
    ship_to TEXT,
    bill_to TEXT,
    notes TEXT,
    vendor_message TEXT,
    drop_ship BOOLEAN DEFAULT FALSE,
    no_tags BOOLEAN DEFAULT FALSE,
    qbo_bill_id TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_po_number ON purchase_orders(po_number);
CREATE INDEX IF NOT EXISTS idx_po_vendor ON purchase_orders(vendor_id);
CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status);

-- Purchase order line items
CREATE TABLE IF NOT EXISTS purchase_order_lines (
    id SERIAL PRIMARY KEY,
    po_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity_ordered INTEGER NOT NULL DEFAULT 0,
    quantity_received INTEGER NOT NULL DEFAULT 0,
    unit_cost DECIMAL(10,2) DEFAULT 0,
    line_total DECIMAL(10,2) DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_po_line_po ON purchase_order_lines(po_id);
CREATE INDEX IF NOT EXISTS idx_po_line_product ON purchase_order_lines(product_id);

-- Customers table
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    company TEXT,
    email TEXT,
    phone TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    country TEXT DEFAULT 'USA',
    payment_terms TEXT DEFAULT 'Net 30',
    tax_exempt BOOLEAN DEFAULT FALSE,
    credit_limit DECIMAL(10,2) DEFAULT 0,
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    qbo_customer_id TEXT,
    shopify_customer_id TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_customers_company ON customers(company);

-- Invoices table
CREATE TABLE IF NOT EXISTS invoices (
    id SERIAL PRIMARY KEY,
    invoice_number TEXT UNIQUE NOT NULL,
    customer_id INTEGER REFERENCES customers(id),
    status TEXT DEFAULT 'draft',
    invoice_date DATE,
    due_date DATE,
    ship_date DATE,
    subtotal DECIMAL(10,2) DEFAULT 0,
    tax_rate DECIMAL(5,4) DEFAULT 0,
    tax_amount DECIMAL(10,2) DEFAULT 0,
    shipping DECIMAL(10,2) DEFAULT 0,
    total DECIMAL(10,2) DEFAULT 0,
    amount_paid DECIMAL(10,2) DEFAULT 0,
    balance_due DECIMAL(10,2) DEFAULT 0,
    payment_method TEXT,
    tracking_number TEXT,
    notes TEXT,
    internal_notes TEXT,
    qbo_invoice_id TEXT,
    shopify_order_id TEXT,
    finalized_at TIMESTAMP,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoices_number ON invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date);

-- Invoice line items
CREATE TABLE IF NOT EXISTS invoice_lines (
    id SERIAL PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id),
    serial_id INTEGER REFERENCES serial_numbers(id),
    sku TEXT,
    description TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price DECIMAL(10,2) DEFAULT 0,
    line_total DECIMAL(10,2) DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoice_line_invoice ON invoice_lines(invoice_id);
CREATE INDEX IF NOT EXISTS idx_invoice_line_product ON invoice_lines(product_id);
CREATE INDEX IF NOT EXISTS idx_invoice_line_serial ON invoice_lines(serial_id);

-- Storage locations table (Phase I: Bin/Location tracking)
CREATE TABLE IF NOT EXISTS storage_locations (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    aisle TEXT,
    shelf TEXT,
    bin TEXT,
    zone TEXT,
    capacity INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_locations_code ON storage_locations(code);
CREATE INDEX IF NOT EXISTS idx_locations_zone ON storage_locations(zone);

-- Product location mapping (allows product in multiple locations)
CREATE TABLE IF NOT EXISTS product_locations (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    location_id INTEGER NOT NULL REFERENCES storage_locations(id) ON DELETE CASCADE,
    quantity INTEGER DEFAULT 0,
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(product_id, location_id)
);

CREATE INDEX IF NOT EXISTS idx_product_locations_product ON product_locations(product_id);
CREATE INDEX IF NOT EXISTS idx_product_locations_location ON product_locations(location_id);

-- Serial/Lot numbers table (Phase I Part 2: Serial tracking)
CREATE TABLE IF NOT EXISTS serial_numbers (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    serial_number TEXT NOT NULL,
    lot_number TEXT,
    status TEXT DEFAULT 'available',
    location_id INTEGER REFERENCES storage_locations(id),
    cost DECIMAL(10,2) DEFAULT 0,
    received_date DATE,
    sold_date DATE,
    expiry_date DATE,
    warranty_expiry DATE,
    invoice_id INTEGER REFERENCES invoices(id),
    po_id INTEGER REFERENCES purchase_orders(id),
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(product_id, serial_number)
);

CREATE INDEX IF NOT EXISTS idx_serial_product ON serial_numbers(product_id);
CREATE INDEX IF NOT EXISTS idx_serial_number ON serial_numbers(serial_number);
CREATE INDEX IF NOT EXISTS idx_serial_status ON serial_numbers(status);
CREATE INDEX IF NOT EXISTS idx_serial_lot ON serial_numbers(lot_number);

-- Core returns table (Phase I Part 3: Core tracking)
CREATE TABLE IF NOT EXISTS core_returns (
    id SERIAL PRIMARY KEY,
    invoice_line_id INTEGER REFERENCES invoice_lines(id) ON DELETE CASCADE,
    invoice_id INTEGER REFERENCES invoices(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    customer_id INTEGER REFERENCES customers(id),
    core_charge DECIMAL(10,2) DEFAULT 0,
    customer_core_price DECIMAL(10,2) DEFAULT 0,
    status TEXT DEFAULT 'pending',
    due_date DATE,
    received_date DATE,
    credit_issued DECIMAL(10,2) DEFAULT 0,
    credit_date DATE,
    credit_invoice_id INTEGER REFERENCES invoices(id),
    serial_number TEXT,
    condition TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_core_invoice ON core_returns(invoice_id);
CREATE INDEX IF NOT EXISTS idx_core_customer ON core_returns(customer_id);
CREATE INDEX IF NOT EXISTS idx_core_product ON core_returns(product_id);
CREATE INDEX IF NOT EXISTS idx_core_status ON core_returns(status);

-- Customer credits ledger (migration 069)
CREATE TABLE IF NOT EXISTS customer_credits (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    source_type TEXT NOT NULL,
    source_id INTEGER,
    amount NUMERIC(10,2) NOT NULL DEFAULT 0,
    applied_to_invoice_id INTEGER REFERENCES invoices(id),
    issue_date TIMESTAMP DEFAULT NOW(),
    applied_date TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'open',
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_customer_credits_customer_status
    ON customer_credits(customer_id, status);
CREATE INDEX IF NOT EXISTS ix_customer_credits_source
    ON customer_credits(source_type, source_id);
CREATE INDEX IF NOT EXISTS ix_customer_credits_applied_invoice
    ON customer_credits(applied_to_invoice_id);

-- Inventory transfers table (Phase I Part 4: Multi-location transfers)
CREATE TABLE IF NOT EXISTS inventory_transfers (
    id SERIAL PRIMARY KEY,
    transfer_number TEXT UNIQUE NOT NULL,
    from_location_id INTEGER NOT NULL REFERENCES storage_locations(id),
    to_location_id INTEGER NOT NULL REFERENCES storage_locations(id),
    status TEXT DEFAULT 'draft',
    requested_date DATE,
    approved_date DATE,
    completed_date DATE,
    requested_by TEXT,
    approved_by TEXT,
    completed_by TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transfer_number ON inventory_transfers(transfer_number);
CREATE INDEX IF NOT EXISTS idx_transfer_from ON inventory_transfers(from_location_id);
CREATE INDEX IF NOT EXISTS idx_transfer_to ON inventory_transfers(to_location_id);
CREATE INDEX IF NOT EXISTS idx_transfer_status ON inventory_transfers(status);

-- Transfer line items
CREATE TABLE IF NOT EXISTS transfer_lines (
    id SERIAL PRIMARY KEY,
    transfer_id INTEGER NOT NULL REFERENCES inventory_transfers(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 1,
    serial_id INTEGER REFERENCES serial_numbers(id),
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transfer_line_transfer ON transfer_lines(transfer_id);
CREATE INDEX IF NOT EXISTS idx_transfer_line_product ON transfer_lines(product_id);

-- Part Interchanges (Phase 2)
CREATE TABLE IF NOT EXISTS part_interchanges (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    interchange_number TEXT NOT NULL,
    interchange_type TEXT NOT NULL,
    manufacturer TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(product_id, interchange_number)
);

CREATE INDEX IF NOT EXISTS idx_interchange_number ON part_interchanges(interchange_number);
CREATE INDEX IF NOT EXISTS idx_interchange_product ON part_interchanges(product_id);

-- Inventory Audits (Phase 1C)
CREATE TABLE IF NOT EXISTS inventory_audits (
    id SERIAL PRIMARY KEY,
    location_id INTEGER REFERENCES storage_locations(id),
    audit_date TIMESTAMP DEFAULT NOW(),
    count_type TEXT DEFAULT 'full',
    status TEXT DEFAULT 'in_progress',
    notes TEXT,
    total_items INTEGER DEFAULT 0,
    total_variances INTEGER DEFAULT 0,
    variance_value DECIMAL(10,2) DEFAULT 0,
    created_by TEXT,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_lines (
    id SERIAL PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES inventory_audits(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    system_qty INTEGER DEFAULT 0,
    counted_qty INTEGER,
    variance INTEGER DEFAULT 0,
    variance_value DECIMAL(10,2) DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_lines_audit ON audit_lines(audit_id);
CREATE INDEX IF NOT EXISTS idx_audit_lines_product ON audit_lines(product_id);

-- QBO Export Tracking (Phase 1D)
CREATE TABLE IF NOT EXISTS qbo_exports (
    id SERIAL PRIMARY KEY,
    export_type TEXT NOT NULL,
    export_format TEXT DEFAULT 'csv',
    record_count INTEGER DEFAULT 0,
    file_path TEXT,
    notes TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- ESN Cache (Phase 4)
CREATE TABLE IF NOT EXISTS esn_cache (
    id SERIAL PRIMARY KEY,
    esn TEXT UNIQUE NOT NULL,
    oem TEXT NOT NULL,
    engine_model TEXT,
    horsepower TEXT,
    build_date TEXT,
    cpl TEXT,
    application TEXT,
    emission_level TEXT,
    raw_data TEXT,
    cached_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_esn_esn ON esn_cache(esn);
CREATE INDEX IF NOT EXISTS idx_esn_oem ON esn_cache(oem);

CREATE TABLE IF NOT EXISTS engine_compatibility (
    id SERIAL PRIMARY KEY,
    engine_model TEXT NOT NULL,
    category TEXT NOT NULL,
    product_id INTEGER REFERENCES products(id),
    oem_part_number TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_engine_compat_model ON engine_compatibility(engine_model);
CREATE INDEX IF NOT EXISTS idx_engine_compat_product ON engine_compatibility(product_id);

-- Work Orders (Phase 5A)
CREATE TABLE IF NOT EXISTS work_orders (
    id SERIAL PRIMARY KEY,
    wo_number TEXT UNIQUE NOT NULL,
    customer_id INTEGER REFERENCES customers(id),
    vehicle_id INTEGER,
    status TEXT DEFAULT 'draft',
    priority TEXT DEFAULT 'normal',
    complaint TEXT,
    diagnosis TEXT,
    technician TEXT,
    estimated_hours DECIMAL(6,2) DEFAULT 0,
    actual_hours DECIMAL(6,2) DEFAULT 0,
    labor_rate DECIMAL(10,2) DEFAULT 0,
    parts_total DECIMAL(10,2) DEFAULT 0,
    labor_total DECIMAL(10,2) DEFAULT 0,
    total DECIMAL(10,2) DEFAULT 0,
    invoice_id INTEGER REFERENCES invoices(id),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    notes TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wo_number ON work_orders(wo_number);
CREATE INDEX IF NOT EXISTS idx_wo_customer ON work_orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_wo_status ON work_orders(status);
CREATE INDEX IF NOT EXISTS idx_wo_vehicle ON work_orders(vehicle_id);

CREATE TABLE IF NOT EXISTS work_order_lines (
    id SERIAL PRIMARY KEY,
    wo_id INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    line_type TEXT NOT NULL DEFAULT 'part',
    product_id INTEGER REFERENCES products(id),
    serial_id INTEGER REFERENCES serial_numbers(id),
    description TEXT,
    quantity INTEGER DEFAULT 1,
    unit_price DECIMAL(10,2) DEFAULT 0,
    line_total DECIMAL(10,2) DEFAULT 0,
    hours DECIMAL(6,2) DEFAULT 0,
    technician TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wol_wo ON work_order_lines(wo_id);
CREATE INDEX IF NOT EXISTS idx_wol_product ON work_order_lines(product_id);

-- Vehicles / Fleet Registry (Phase 5B)
CREATE TABLE IF NOT EXISTS vehicles (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    unit_number TEXT,
    vin TEXT,
    year INTEGER,
    make TEXT,
    model TEXT,
    engine_make TEXT,
    engine_model TEXT,
    esn TEXT,
    mileage INTEGER DEFAULT 0,
    license_plate TEXT,
    dot_number TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vehicles_customer ON vehicles(customer_id);
CREATE INDEX IF NOT EXISTS idx_vehicles_vin ON vehicles(vin);
CREATE INDEX IF NOT EXISTS idx_vehicles_unit ON vehicles(unit_number);
CREATE INDEX IF NOT EXISTS idx_vehicles_esn ON vehicles(esn);

-- Returns & Warranty (Phase 5E)
CREATE TABLE IF NOT EXISTS returns (
    id SERIAL PRIMARY KEY,
    rma_number TEXT UNIQUE NOT NULL,
    invoice_id INTEGER REFERENCES invoices(id),
    customer_id INTEGER REFERENCES customers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    serial_id INTEGER REFERENCES serial_numbers(id),
    quantity INTEGER DEFAULT 1,
    reason TEXT,
    condition TEXT,
    status TEXT DEFAULT 'pending',
    action_taken TEXT,
    refund_amount DECIMAL(10,2) DEFAULT 0,
    vendor_claim_id TEXT,
    vendor_claim_status TEXT,
    warranty_expiry DATE,
    notes TEXT,
    received_date DATE,
    resolved_date DATE,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_returns_rma ON returns(rma_number);
CREATE INDEX IF NOT EXISTS idx_returns_customer ON returns(customer_id);
CREATE INDEX IF NOT EXISTS idx_returns_product ON returns(product_id);
CREATE INDEX IF NOT EXISTS idx_returns_status ON returns(status);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for products table
DROP TRIGGER IF EXISTS update_products_updated_at ON products;
CREATE TRIGGER update_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""


def get_schema(backend: str) -> str:
    """Get the appropriate schema for the backend."""
    if backend == "postgresql":
        return POSTGRESQL_SCHEMA
    return SQLITE_SCHEMA
