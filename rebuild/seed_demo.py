"""Seed one customer + 3 products + 1 draft quote for the demo tour."""
import sys
sys.path.insert(0, ".")

from datetime import datetime, timedelta
from app.database import SessionLocal
from app.models.customer import Customer
from app.models.quote import Quote, QuoteLine
from app.models.product import Product
from app.models.vendor import Vendor
from app.constants import LineRole, LineType, QuoteStatus, QuoteOutcome
from app.settings_utils import bump_counter

db = SessionLocal()

# ── Vendor ───────────────────────────────────────────────────────────────────
v = Vendor(name="PAI Industries", contact_name="Sales Desk",
           phone="800-555-1234", email="sales@paiind.com",
           account_number="JAK-001", is_active=True)
db.add(v)
db.flush()

# ── Products ─────────────────────────────────────────────────────────────────
p1 = Product(sku="PAI-311148", title="Inframe Kit ISX - Comprehensive Rebuild",
             manufacturer="PAI", cost=735.00, markup_pct=29.2,
             qty_on_hand=3, reorder_point=1, is_active=True)
p2 = Product(sku="PAI-285040", title="Head Gasket ISX - Multi-Layer Steel",
             manufacturer="PAI", cost=138.00, markup_pct=19.6,
             qty_on_hand=8, reorder_point=2, is_active=True)
p3 = Product(sku="PAI-111872", title="Oil Pump ISX - High Volume",
             manufacturer="PAI", cost=185.00, markup_pct=24.5,
             qty_on_hand=5, reorder_point=1, is_active=True)
db.add_all([p1, p2, p3])
db.flush()

# ── Customer ─────────────────────────────────────────────────────────────────
c = Customer(
    company_name="Mike's Diesel Repair",
    contact_name="Mike Johnson",
    phone="(713) 555-9800",
    email="mike@mikesdiesel.com",
    address_line1="4521 Gulf Fwy",
    city="Houston", state="TX", zip_code="77023",
    payment_terms="NET 30",
    is_tax_exempt=True,
    discount_pct=0.0,
)
db.add(c)
db.flush()

# ── Quote ─────────────────────────────────────────────────────────────────────
year = datetime.utcnow().year
quote_number = bump_counter(db, "next_quote_number", "Q", year)
q = Quote(
    quote_number=quote_number,
    customer_id=c.id,
    status=QuoteStatus.DRAFT,
    outcome=QuoteOutcome.PENDING,
    discount_pct=0.0,
    validity_days=30,
    valid_until=datetime.utcnow() + timedelta(days=30),
    notes="",
)
db.add(q)
db.flush()

# ── Lines ─────────────────────────────────────────────────────────────────────
l1 = QuoteLine(
    quote_id=q.id, product_id=p1.id,
    line_type=LineType.PRODUCT, line_role=LineRole.PRIMARY,
    description="Inframe Kit ISX - Comprehensive Rebuild",
    qty=1, unit_price=950.00, unit_cost=735.00,
    is_included=True, sort_order=0,
)
l2 = QuoteLine(
    quote_id=q.id, product_id=p2.id,
    line_type=LineType.PRODUCT, line_role=LineRole.PRIMARY,
    description="Head Gasket ISX - Multi-Layer Steel",
    qty=1, unit_price=165.00, unit_cost=138.00,
    is_included=True, sort_order=1,
)
l3 = QuoteLine(
    quote_id=q.id, product_id=p3.id,
    line_type=LineType.PRODUCT, line_role=LineRole.UPGRADE_OPTION,
    description="Oil Pump ISX - High Volume (upgrade option)",
    qty=1, unit_price=245.00, unit_cost=185.00,
    is_included=False, sort_order=2,
)
db.add_all([l1, l2, l3])
db.commit()

print(f"Done — customer_id={c.id}  quote_id={q.id}  quote_number={q.quote_number}")
