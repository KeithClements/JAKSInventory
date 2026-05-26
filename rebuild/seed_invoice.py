"""Seed a demo invoice by converting quote #1 via InvoiceService."""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.services.invoice_service import InvoiceService

db = SessionLocal()

# InvoiceService needs a user_id — use 1 (the demo user created earlier)
svc = InvoiceService(db, current_user_id=1)

# Create a direct invoice for customer 1 (Mike's Diesel Repair)
from app.constants import LineType
invoice = svc.create_invoice(
    customer_id=1,
    data={
        "customer_po_number": "PO-55123",
        "esn": "79A55839",
        "engine_manufacturer": "Cummins",
        "engine_model": "ISX15",
        "discount_pct": 0.0,
        "is_taxable": False,
        "tax_rate": 0.0,
        "apply_cc_surcharge": False,
        "cc_surcharge_pct": 3.0,
        "notes": "Ship to shop address on file. Call Mike before delivery.",
    },
    lines=[
        {
            "product_id": 1,
            "description": "Inframe Kit ISX - Comprehensive Rebuild",
            "qty": 1,
            "unit_price": 950.00,
            "unit_cost": 735.00,
            "line_type": LineType.PRODUCT,
            "discount_pct": 0.0,
        },
        {
            "product_id": 2,
            "description": "Head Gasket ISX - Multi-Layer Steel",
            "qty": 1,
            "unit_price": 165.00,
            "unit_cost": 138.00,
            "line_type": LineType.PRODUCT,
            "discount_pct": 0.0,
        },
    ],
)

print(f"Created invoice: {invoice.invoice_number}  id={invoice.id}  status={invoice.status}")
