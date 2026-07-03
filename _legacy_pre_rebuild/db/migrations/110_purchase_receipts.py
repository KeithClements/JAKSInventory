"""
Migration 110: Add purchase_receipts and purchase_receipt_lines tables for formal receiving workflow.
"""

def upgrade(conn):
    conn.executescript('''
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
    ''')

def downgrade(conn):
    conn.executescript('''
    DROP TABLE IF EXISTS purchase_receipt_lines;
    DROP TABLE IF EXISTS purchase_receipts;
    ''')
