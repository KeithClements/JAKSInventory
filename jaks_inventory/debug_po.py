"""Debug PO creation."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATABASE_URL", "sqlite:///c:/Users/keith/Website/jaks_scraper/output/inventory.db")

from db.database import get_db
from db.inventory import generate_po_number
from datetime import datetime

# Replicate create_purchase_order logic manually
po_number = generate_po_number()
order_date = datetime.now().strftime("%Y-%m-%d")
vendor_id = 21
print(f"PO Number: {po_number}, Vendor: {vendor_id}, Date: {order_date}")

with get_db() as conn:
    try:
        conn.execute("""
            INSERT INTO purchase_orders (po_number, vendor_id, status, order_date, expected_date, notes)
            VALUES (%s, %s, 'draft', %s, %s, %s)
        """, (po_number, vendor_id, order_date, None, "debug test"))
        print("INSERT succeeded")
        
        po_row = conn.execute("SELECT id FROM purchase_orders WHERE po_number = %s", (po_number,)).fetchone()
        print(f"po_row: {po_row}")
        po_id = po_row[0] if po_row else None
        print(f"po_id: {po_id!r} (type: {type(po_id).__name__})")
        
        if not po_id:
            print("po_id is falsy!")
        
        conn.commit()
    except Exception as e:
        import traceback
        print(f"Exception type: {type(e).__name__}")
        print(f"Exception str: {str(e)!r}")
        traceback.print_exc()

# Check what's in the table
with get_db() as conn:
    rows = conn.execute("SELECT id, po_number, vendor_id FROM purchase_orders").fetchall()
    print(f"\nPO table has {len(rows)} rows")
    for r in rows:
        print(f"  {dict(r)}")

