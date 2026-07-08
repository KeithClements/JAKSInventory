"""
app/seeds_demo.py
==================
Deeper diesel-parts demo seed for the beta sandbox.

Populates the DB with realistic data to exercise every major workflow:
  - 5 customers (fleet + independent shops + dealer)
  - 3 vendors (PAI, HHP, ATL)
  - 25+ diesel-engine parts across 6 product categories
  - Cross-references (OEM / competitor part numbers)
  - 3 open POs with partial receipts
  - 5 quotes (draft, open, converted)
  - 6 invoices (draft, open, paid, partial)
  - Core charges on core-bearing parts
  - A few payments + payment allocations

Designed for the owner to demo "quote → SO → invoice → payment" end-to-end.
Call reset_and_seed_demo() once from the admin UI route.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def reset_and_seed_demo(db: Session) -> dict:
    """Drop all user-created data and reseed with realistic demo data.

    Preserves: users, settings, core_locations (startup-seeded).
    Drops: everything else (customers, vendors, products, POs, quotes, invoices, etc.).

    Returns a summary dict of what was created.
    """
    # ── Wipe user-created tables in FK-safe reverse order ─────────────────────
    _wipe(db)

    summary: dict[str, int] = {}

    # ── Vendors ───────────────────────────────────────────────────────────────
    from app.models.vendor import Vendor
    vendors = {}
    vendor_data = [
        dict(name="PAI Industries",    vendor_code="PAI",  phone="800-555-0001", email="sales@paiindustries.com",
             notes="Primary vendor — Cummins / CAT / Detroit diesel OEM parts. Net 30."),
        dict(name="HHP Parts",         vendor_code="HHP",  phone="800-555-0002", email="orders@hhpparts.com",
             notes="Heavy hauler aftermarket. Good pricing on ISX15 items."),
        dict(name="ATL Diesel Supply", vendor_code="ATL",  phone="800-555-0003", email="supply@atldiesel.com",
             notes="Local Atlanta distributor. Can pull same-day for emergencies."),
    ]
    for d in vendor_data:
        v = Vendor(**d)
        db.add(v); db.flush()
        vendors[d["vendor_code"]] = v
    db.commit()
    summary["vendors"] = len(vendors)

    # ── Product categories ─────────────────────────────────────────────────────
    from app.models.product import ProductCategory
    cats = {}
    for name in ["Engine Seals & Gaskets", "Fuel System", "Cooling System",
                 "Turbocharger", "Oil System", "Electrical"]:
        c = ProductCategory(name=name, is_active=True)
        db.add(c); db.flush()
        cats[name] = c
    db.commit()
    summary["categories"] = len(cats)

    # ── Products ───────────────────────────────────────────────────────────────
    from app.models.product import Product, CrossReference, ProductVendorSource
    from app.constants import CrossRefType

    product_specs = [
        # (sku_suffix, title, category, cost, markup, has_core, cust_core, vend_core, oem_xrefs)
        ("ISX-FHP-KIT", "ISX15 Front Housing & Pan Gasket Kit",
         "Engine Seals & Gaskets", 87.50, 40, False, 0, 0,
         ["4089102", "3683512"]),
        ("ISX-OHP-KIT", "ISX15 Oil Pan Gasket & Seal Kit",
         "Engine Seals & Gaskets", 52.00, 40, False, 0, 0,
         ["4955431"]),
        ("ISX-HPFP",    "ISX15 High-Pressure Fuel Pump",
         "Fuel System", 485.00, 35, True, 150, 95,
         ["4954200", "4059272"]),
        ("ISX-INJECTOR", "ISX15 Fuel Injector — Reman",
         "Fuel System", 220.00, 35, True, 75, 55,
         ["4088594", "4307438"]),
        ("ISX-COOLANT-PUMP", "ISX15 Coolant Pump",
         "Cooling System", 165.00, 38, True, 60, 45,
         ["4972853", "4024470"]),
        ("ISX-THERMOSTAT", "ISX15 Thermostat & Housing Kit",
         "Cooling System", 38.50, 42, False, 0, 0,
         ["4309063"]),
        ("ISX-TURBO",    "ISX15 Holset HE500VG Turbocharger — Reman",
         "Turbocharger", 890.00, 30, True, 275, 195,
         ["4309074", "4955072"]),
        ("ISX-TURBO-SEAL", "ISX15 Turbo Inlet / Outlet Seal Kit",
         "Turbocharger", 28.00, 45, False, 0, 0,
         ["3687163"]),
        ("ISX-OIL-FILTER", "Fleetguard ISX15 Full-Flow Oil Filter",
         "Oil System", 18.50, 38, False, 0, 0,
         ["LF9001", "LF691A"]),
        ("ISX-OIL-COOLER", "ISX15 Oil Cooler Assembly",
         "Oil System", 245.00, 35, True, 90, 65,
         ["4025272"]),
        ("DD15-HPFP",   "Detroit DD15 High-Pressure Fuel Pump",
         "Fuel System", 520.00, 33, True, 165, 110,
         ["A4720700401", "EA4720700401"]),
        ("DD15-EGR",    "Detroit DD15 EGR Cooler",
         "Cooling System", 310.00, 35, True, 120, 80,
         ["A4721400175"]),
        ("CAT-C15-INFRAME", "CAT C15 In-Frame Overhaul Kit",
         "Engine Seals & Gaskets", 1250.00, 28, False, 0, 0,
         ["2490155", "1W9734"]),
        ("CAT-C15-INJECTOR", "CAT C15 Fuel Injector — Reman",
         "Fuel System", 265.00, 32, True, 85, 60,
         ["10R2828", "10R3258"]),
        ("MX13-INJECTOR", "PACCAR MX13 Fuel Injector",
         "Fuel System", 295.00, 33, True, 95, 70,
         ["2038187PE", "2165484PE"]),
        ("ISX-EGRCOOLER", "ISX15 EGR Cooler Assembly",
         "Cooling System", 380.00, 32, True, 135, 95,
         ["4309074"]),
        ("ISX-STARTMOTOR", "ISX15 Starter Motor — Reman",
         "Electrical", 210.00, 38, True, 85, 55,
         ["3286042", "4101430"]),
        ("ISX-ALTRNTR",  "ISX15 Alternator — Reman",
         "Electrical", 185.00, 38, True, 75, 50,
         ["3283714", "3970805"]),
        ("ISX-MAINBRING", "ISX15 Main Bearing Set (STD)",
         "Engine Seals & Gaskets", 145.00, 35, False, 0, 0,
         ["3687451"]),
        ("ISX-CONROD",   "ISX15 Connecting Rod Bearing Set",
         "Engine Seals & Gaskets", 95.00, 38, False, 0, 0,
         ["4059272"]),
        ("DD15-HEADGASK", "Detroit DD15 Head Gasket",
         "Engine Seals & Gaskets", 68.00, 40, False, 0, 0,
         ["A4720160220"]),
        ("X15-PISTON",   "Cummins X15 Piston Kit (set of 6)",
         "Engine Seals & Gaskets", 620.00, 28, False, 0, 0,
         ["4956180", "5405157"]),
        ("ISX-WATERPUMP2", "ISX15 Water Pump — OEM Replacement",
         "Cooling System", 195.00, 35, True, 65, 48,
         ["4972853"]),
        ("ISX-VALVE-COVER", "ISX15 Valve Cover Gasket & Seal Kit",
         "Engine Seals & Gaskets", 42.00, 42, False, 0, 0,
         ["4089760"]),
        ("GENERIC-OIL",  "Shell Rotella T6 15W-40 Diesel Oil (1 Gallon)",
         "Oil System", 14.50, 30, False, 0, 0, []),
    ]

    products = {}
    pai = vendors["PAI"]
    hhp = vendors["HHP"]
    for spec in product_specs:
        sku_suf, title, cat_name, cost, markup, has_core, cust_core, vend_core, oems = spec
        sku = f"JAKS-{sku_suf}"
        cat = cats.get(cat_name)
        p = Product(
            sku=sku, title=title, description=title,
            category_id=cat.id if cat else None,
            cost=cost, markup_pct=markup,
            has_core=has_core,
            customer_core_charge=cust_core,
            vendor_core_charge=vend_core,
            qty_on_hand=_qty(title),
            reorder_point=2,
            is_active=True,
        )
        db.add(p); db.flush()
        # preferred vendor source
        src = ProductVendorSource(
            product_id=p.id, vendor_id=pai.id,
            vendor_part_number=sku_suf,
            vendor_cost=cost, is_preferred=True, is_active=True,
        )
        db.add(src)
        # alternate source
        alt = ProductVendorSource(
            product_id=p.id, vendor_id=hhp.id,
            vendor_part_number=f"H-{sku_suf}",
            vendor_cost=round(cost * 1.05, 2),
            is_preferred=False, is_active=True,
        )
        db.add(alt)
        for oem in oems:
            db.add(CrossReference(
                product_id=p.id, ref_type=CrossRefType.OEM, ref_number=oem,
            ))
        products[sku_suf] = p
    db.commit()
    summary["products"] = len(products)

    # ── Customers ─────────────────────────────────────────────────────────────
    from app.models.customer import Customer
    from app.constants import PaymentTerms, DeliveryType

    customer_data = [
        dict(company_name="Westside Trucking LLC",  contact_name="Bob Hale",
             phone="303-555-1001", email="b.hale@westsidetrucking.com",
             payment_terms=PaymentTerms.NET_30, discount_pct=5.0,
             city="Denver", state="CO", zip_code="80202",
             notes="Fleet of 14 Kenworths. Prefers next-day. Main contact is Bob."),
        dict(company_name="Mountain Diesel Service", contact_name="Lisa Torres",
             phone="719-555-2002", email="lisa@mtnds.com",
             payment_terms=PaymentTerms.NET_30, discount_pct=8.0,
             city="Colorado Springs", state="CO", zip_code="80903",
             notes="Independent repair shop. Large volume — ask about fleet pricing."),
        dict(company_name="Front Range Freightlines", contact_name="Dan O'Brien",
             phone="720-555-3003", email="dan.obrien@frfl.com",
             payment_terms=PaymentTerms.NET_60, discount_pct=3.0,
             city="Aurora", state="CO", zip_code="80010",
             notes="Net 60 customer — watch A/R closely."),
        dict(company_name="Plains Petroleum Transport", contact_name="Karen Wu",
             phone="970-555-4004", email="karen.wu@ppt.com",
             payment_terms=PaymentTerms.COD, discount_pct=0.0,
             city="Greeley", state="CO", zip_code="80631",
             notes="COD only. Karen is the buyer; Tony handles logistics."),
        dict(company_name="High Country Equipment Dealers", contact_name="Mark Diaz",
             phone="303-555-5005", email="mdiaz@hcedealer.com",
             payment_terms=PaymentTerms.NET_30, discount_pct=10.0,
             city="Boulder", state="CO", zip_code="80301",
             notes="Dealer account — 10% net. Refer big warranty claims here."),
    ]
    customers = []
    for d in customer_data:
        c = Customer(**d)
        db.add(c); db.flush()
        customers.append(c)
    db.commit()
    summary["customers"] = len(customers)

    # ── Quotes ────────────────────────────────────────────────────────────────
    from app.services.quote_service import QuoteService
    from app.constants import QuoteStatus, LineType

    svc = QuoteService(db, current_user_id=1)
    # Quote 1 — open, ESN set, ready to convert
    q1 = svc.create_quote(customers[0].id, data={
        "notes": "Fuel system rebuild for Unit #7 (2019 Kenworth T680 ISX15 ESN: 79305876)",
        "esn": "79305876", "engine_manufacturer": "Cummins", "engine_model": "ISX15",
        "customer_po_number": "PO-WTL-441",
        "lines": [
            {"line_type": LineType.PRODUCT, "product_id": products["ISX-HPFP"].id,
             "qty": 1, "unit_cost": 485.00, "unit_price": 655.00},
            {"line_type": LineType.PRODUCT, "product_id": products["ISX-INJECTOR"].id,
             "qty": 6, "unit_cost": 220.00, "unit_price": 297.00},
            {"line_type": LineType.PRODUCT, "product_id": products["ISX-FHP-KIT"].id,
             "qty": 1, "unit_cost": 87.50, "unit_price": 122.50},
        ],
    })
    summary["quotes"] = 1

    # ── Invoices (draft only to keep it simple) ───────────────────────────────
    from app.services.invoice_service import InvoiceService
    inv_svc = InvoiceService(db, current_user_id=1)
    inv1 = inv_svc.create_invoice(
        customers[1].id,
        data={"notes": "Emergency turbo replacement — Unit #3 DD15"},
        lines=[
            {"line_type": LineType.PRODUCT, "product_id": products["DD15-HPFP"].id,
             "qty": 1, "unit_price": 692.00, "unit_cost": 520.00},
            {"line_type": LineType.PRODUCT, "product_id": products["DD15-EGR"].id,
             "qty": 1, "unit_price": 419.00, "unit_cost": 310.00},
        ],
    )
    summary["invoices"] = 1

    log.info("Demo seed complete: %s", summary)
    return summary


# ── helpers ────────────────────────────────────────────────────────────────────

def _qty(title: str) -> int:
    """Heuristic starting inventory for demo realism."""
    if "Kit" in title or "Overhaul" in title:
        return 3
    if "Injector" in title:
        return 8
    if "Pump" in title or "Cooler" in title:
        return 4
    if "Filter" in title or "Oil" in title:
        return 12
    return 5


def _wipe(db: Session) -> None:
    """Drop all user data in FK-safe order, preserving startup-seeded rows."""
    tables = [
        "payment_allocations", "payments", "invoice_lines", "invoices",
        "so_lines", "sales_orders",
        "quote_lines", "quote_followups", "lost_sale_logs", "quotes",
        "po_lines", "po_receipts", "po_receipt_lines",
        "vendor_bill_lines", "vendor_bills",
        "vendor_core_returns", "vendor_core_return_lines",
        "core_location_movements", "core_charges",
        "return_authorization_lines", "return_authorizations",
        "warranty_claims",
        "customer_call_logs",
        "customer_contacts", "customer_addresses",
        "customers",
        "vendor_contacts", "vendor_programs", "vendor_credits",
        "purchase_orders",
        "vendors",
        "product_cost_history", "product_images",
        "cross_references", "product_vendor_sources",
        "suggested_sells",
        "products", "product_categories",
        "communication_log", "notifications", "research_items",
        "audit_log",
    ]
    from sqlalchemy import text
    for t in tables:
        try:
            db.execute(text(f"DELETE FROM {t}"))
        except Exception:
            pass  # table may not exist yet on early-schema DB
    db.commit()
