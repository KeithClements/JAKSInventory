"""Seed the database with realistic heavy-duty diesel parts test data."""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta
from db import (
    create_vendor, create_customer, create_location,
    create_invoice, add_invoice_line, finalize_invoice, record_payment,
    create_purchase_order, add_po_line, update_po_status,
    set_setting, get_setting,
)
from db.database import get_db

now = datetime.now()

# ── Company Settings ──────────────────────────────────────────────
settings = {
    "company_name": "JAK'S Diesel",
    "company_address": "489 Parkson Rd",
    "company_city": "Henderson",
    "company_state": "NV",
    "company_zip": "89011",
    "company_phone": "(702) 547-3070",
    "company_email": "parts@jaksdiesel.com",
    "company_website": "www.jaksdiesel.com",
    "default_tax_rate": "8.375",
    "payment_terms": "Net 30",
    "invoice_prefix": "INV-",
    "invoice_notes": "Thank you for your business!",
    "low_stock_threshold": "5",
}
for k, v in settings.items():
    set_setting(k, v)
print(f"Settings: {len(settings)} saved")

# ── Vendors ───────────────────────────────────────────────────────
vendors_data = [
    ("PAI Industries", "PAI", "Mike Torres", "mike@paiind.com", "(832) 555-0101", "Houston, TX", "Net 30"),
    ("Interstate McBee", "MCBEE", "Sarah Chen", "sarah@mcbee.com", "(214) 555-0202", "Dallas, TX", "Net 30"),
    ("Dorman HD Solutions", "DORMAN", "Jim Kraft", "jim@dorman.com", "(610) 555-0303", "Colmar, PA", "Net 45"),
    ("Maxiforce", "MAXI", "Lisa Park", "lisa@maxiforce.com", "(305) 555-0404", "Miami, FL", "Net 30"),
    ("FP Diesel", "FPD", "Tom Walsh", "tom@fpdiesel.com", "(269) 555-0505", "Kalamazoo, MI", "Net 30"),
    ("IPD Parts", "IPD", "Dave Ruiz", "dave@ipdparts.com", "(714) 555-0606", "Torrance, CA", "Net 45"),
    ("Cummins Filtration", "CUMFILT", "Amy Long", "amy@cumminsfilt.com", "(615) 555-0707", "Nashville, TN", "Net 30"),
    ("Donaldson", "DONALD", "Rick Stein", "rick@donaldson.com", "(952) 555-0808", "Minneapolis, MN", "Net 30"),
    ("National Truck Parts", "NTP", "Carlos Vega", "carlos@ntp.com", "(800) 555-0909", "Atlanta, GA", "Net 30"),
    ("BorgWarner", "BORG", "Kelly Nash", "kelly@borgwarner.com", "(248) 555-1010", "Auburn Hills, MI", "Net 45"),
]

vendor_ids = {}
for name, code, contact, email, phone, addr, terms in vendors_data:
    result = create_vendor(name=name, code=code, contact_name=contact,
                           contact_email=email, contact_phone=phone,
                           address=addr, payment_terms=terms)
    if result.get("success"):
        vendor_ids[code] = result["vendor"]["id"]
    else:
        print(f"  WARN vendor {name}: {result.get('error')}")
print(f"Vendors: {len(vendor_ids)} created")

# ── Customers ─────────────────────────────────────────────────────
customers_data = [
    ("Southwest Freight Lines", "Southwest Freight Lines", "Robert Garcia", "rob@swfreight.com", "(702) 555-1100", "1200 Industrial Blvd", "Las Vegas", "NV", "89118", "Net 30"),
    ("Desert Haulers Inc", "Desert Haulers Inc", "Maria Santos", "maria@deserthaulers.com", "(702) 555-1200", "3400 Tropicana Ave", "Las Vegas", "NV", "89121", "Net 30"),
    ("Silver State Trucking", "Silver State Trucking", "Dan White", "dan@silverstate.com", "(775) 555-1300", "890 Freedom Way", "Reno", "NV", "89502", "Net 45"),
    ("Henderson Fleet Services", "Henderson Fleet Services", "Mike Johnson", "mike@hendersonfleet.com", "(702) 555-1400", "2100 Warm Springs Rd", "Henderson", "NV", "89014", "Net 30"),
    ("Pacific Coast Carriers", "Pacific Coast Carriers", "Jenny Lee", "jenny@pcccarriers.com", "(310) 555-1500", "500 Harbor Blvd", "Long Beach", "CA", "90802", "Net 45"),
    ("Mojave Express", "Mojave Express", "Steve Brown", "steve@mojaveexpress.com", "(760) 555-1600", "12 Desert View Dr", "Barstow", "CA", "92311", "Net 30"),
    ("Great Basin Transport", "Great Basin Transport", "Linda Chen", "linda@gbtransport.com", "(801) 555-1700", "780 Industrial Park", "Salt Lake City", "UT", "84104", "Net 30"),
    ("Kingman Diesel Repair", "Kingman Diesel Repair", "Joe Martinez", "joe@kingmandiesel.com", "(928) 555-1800", "340 Andy Devine Ave", "Kingman", "AZ", "86401", "Due on Receipt"),
    ("Valley Concrete Pumping", "Valley Concrete Pumping", "Tony Russo", "tony@valleyconcrete.com", "(702) 555-1900", "6700 Boulder Hwy", "Las Vegas", "NV", "89122", "Net 30"),
    ("Mesquite Trucking Co", "Mesquite Trucking Co", "Sarah Adams", "sarah@mesquitetrucking.com", "(702) 555-2000", "100 Pioneer Blvd", "Mesquite", "NV", "89027", "Net 30"),
    ("Apex Waste Haulers", "Apex Waste Haulers", "Ray Patel", "ray@apexwaste.com", "(702) 555-2100", "4500 N Las Vegas Blvd", "North Las Vegas", "NV", "89031", "Net 30"),
    ("Zion Logistics", "Zion Logistics", "Amanda Clark", "amanda@zionlogistics.com", "(435) 555-2200", "225 Commerce Dr", "St George", "UT", "84790", "Net 45"),
    ("Cash Customer", "", "", "", "(702) 547-3070", "Walk-in", "Henderson", "NV", "89011", "Due on Receipt"),
    ("Arizona Heavy Haul", "Arizona Heavy Haul", "Pete Wilson", "pete@azheavyhaul.com", "(602) 555-2400", "1800 Grand Ave", "Phoenix", "AZ", "85007", "Net 30"),
    ("Sierra Pacific Fleet", "Sierra Pacific Fleet", "Nancy Yu", "nancy@sierrapacific.com", "(775) 555-2500", "450 Kietzke Ln", "Reno", "NV", "89502", "Net 30"),
]

cust_ids = {}
for name, company, contact, email, phone, addr, city, state, zipcode, terms in customers_data:
    cid = create_customer(name=name, company=company, email=email,
                          phone=phone, address=addr, city=city,
                          state=state, zip_code=zipcode, payment_terms=terms)
    cust_ids[name] = cid
print(f"Customers: {len(cust_ids)} created")

# ── Locations ─────────────────────────────────────────────────────
locations_data = [
    ("Main Warehouse", "WH-MAIN", "A", "1", "", "Main"),
    ("Overstock", "WH-OVER", "B", "1", "", "Main"),
    ("Showroom", "SHOW", "C", "1", "", "Front"),
    ("Returns Cage", "RET", "D", "1", "", "Back"),
    ("Core Staging", "CORE", "D", "2", "", "Back"),
]
loc_ids = {}
for name, code, aisle, shelf, bin_, zone in locations_data:
    lid = create_location(name=name, code=code, aisle=aisle, shelf=shelf,
                          bin=bin_, zone=zone)
    loc_ids[code] = lid
print(f"Locations: {len(loc_ids)} created")

# ── Products ──────────────────────────────────────────────────────
products_data = [
    # (sku, title, category, manufacturer, cost, price, qty, core, vendor_code)
    ("PAI-681800E", "Water Pump - Cummins ISX 15", "Water Pumps", "PAI Industries", 185.00, 329.95, 8, 75.00, "PAI"),
    ("PAI-181800", "Water Pump Kit - N14", "Water Pumps", "PAI Industries", 165.00, 289.95, 4, 50.00, "PAI"),
    ("MCBEE-M-3803703", "Injector - Cummins N14 Celect", "Fuel Injectors", "Interstate McBee", 245.00, 449.95, 12, 100.00, "MCBEE"),
    ("MCBEE-M-4928260", "Injector - ISX CM870", "Fuel Injectors", "Interstate McBee", 310.00, 549.95, 6, 125.00, "MCBEE"),
    ("PAI-350620", "Turbo Cartridge - Cummins ISX", "Turbochargers", "PAI Industries", 420.00, 749.95, 3, 200.00, "PAI"),
    ("BORG-179514", "Turbocharger Reman - S400 SX", "Turbochargers", "BorgWarner", 1250.00, 1899.95, 2, 500.00, "BORG"),
    ("PAI-060037", "Oil Pump - Cummins ISX", "Oil System", "PAI Industries", 195.00, 359.95, 5, 0.00, "PAI"),
    ("MAXI-2251117", "Head Gasket Set - Cummins ISX", "Gaskets", "Maxiforce", 285.00, 479.95, 7, 0.00, "MAXI"),
    ("MAXI-2250125", "Inframe Kit - N14", "Engine Kits", "Maxiforce", 1850.00, 2899.95, 2, 0.00, "MAXI"),
    ("MAXI-2250187", "Inframe Kit - ISX CM870", "Engine Kits", "Maxiforce", 2100.00, 3299.95, 1, 0.00, "MAXI"),
    ("PAI-451400", "Cylinder Liner - ISX", "Cylinder Components", "PAI Industries", 89.00, 159.95, 18, 25.00, "PAI"),
    ("PAI-451505", "Piston Kit - ISX 15", "Cylinder Components", "PAI Industries", 175.00, 299.95, 12, 35.00, "PAI"),
    ("CUMF-LF9009", "Oil Filter - Cummins ISX", "Filters", "Cummins Filtration", 12.50, 24.95, 48, 0.00, "CUMFILT"),
    ("CUMF-FF5580", "Fuel Filter - Cummins ISX", "Filters", "Cummins Filtration", 18.00, 34.95, 36, 0.00, "CUMFILT"),
    ("CUMF-AF25962", "Air Filter - ISX Primary", "Filters", "Cummins Filtration", 42.00, 79.95, 20, 0.00, "CUMFILT"),
    ("DON-P551100", "Oil Filter - Donaldson", "Filters", "Donaldson", 14.00, 27.95, 24, 0.00, "DONALD"),
    ("DON-P550848", "Fuel Filter - Donaldson", "Filters", "Donaldson", 16.00, 31.95, 30, 0.00, "DONALD"),
    ("DORM-904-5004", "EGR Valve - Cummins ISX", "Emissions", "Dorman HD Solutions", 285.00, 489.95, 4, 150.00, "DORMAN"),
    ("DORM-904-5002", "EGR Cooler - ISX", "Emissions", "Dorman HD Solutions", 425.00, 699.95, 3, 175.00, "DORMAN"),
    ("FPD-1830629C93", "Injector Reman - DT466", "Fuel Injectors", "FP Diesel", 195.00, 349.95, 8, 85.00, "FPD"),
    ("IPD-1842570C96", "Head Gasket - DT466E", "Gaskets", "IPD Parts", 125.00, 219.95, 6, 0.00, "IPD"),
    ("PAI-321325", "Starter - Cummins ISX 12V", "Electrical", "PAI Industries", 195.00, 349.95, 4, 75.00, "PAI"),
    ("PAI-321402", "Alternator - Cummins 160A", "Electrical", "PAI Industries", 210.00, 379.95, 3, 65.00, "PAI"),
    ("NTP-550210", "Fan Clutch - Cummins ISX", "Cooling", "National Truck Parts", 285.00, 479.95, 5, 100.00, "NTP"),
    ("NTP-550320", "Thermostat Kit - Cummins", "Cooling", "National Truck Parts", 28.00, 54.95, 15, 0.00, "NTP"),
    ("PAI-050090", "Fuel Transfer Pump - ISX", "Fuel System", "PAI Industries", 145.00, 259.95, 6, 40.00, "PAI"),
    ("MCBEE-M-4089981", "Fuel Pump - Cummins PT", "Fuel System", "Interstate McBee", 850.00, 1399.95, 2, 350.00, "MCBEE"),
    ("PAI-136085", "Air Compressor - SS296E", "Air System", "PAI Industries", 425.00, 699.95, 3, 175.00, "PAI"),
    ("DORM-899-5111", "Power Steering Pump - ISX", "Steering", "Dorman HD Solutions", 195.00, 349.95, 4, 60.00, "DORMAN"),
    ("PAI-480200", "Exhaust Manifold - ISX", "Exhaust", "PAI Industries", 165.00, 289.95, 5, 0.00, "PAI"),
    ("PAI-050105", "Rocker Arm Assembly - ISX", "Valve Train", "PAI Industries", 85.00, 149.95, 10, 0.00, "PAI"),
    ("MAXI-2251302", "Valve Cover Gasket - ISX", "Gaskets", "Maxiforce", 22.00, 42.95, 25, 0.00, "MAXI"),
    ("PAI-060101", "Oil Cooler - Cummins ISX", "Oil System", "PAI Industries", 225.00, 399.95, 4, 85.00, "PAI"),
    ("CUMF-CV52001", "Crankcase Breather Filter", "Filters", "Cummins Filtration", 35.00, 64.95, 14, 0.00, "CUMFILT"),
    ("NTP-601100", "Radiator - Freightliner M2", "Cooling", "National Truck Parts", 450.00, 749.95, 2, 0.00, "NTP"),
    ("PAI-681806", "Water Pump - DD15 Detroit", "Water Pumps", "PAI Industries", 195.00, 349.95, 4, 75.00, "PAI"),
    ("MCBEE-M-R631416", "Injector Reman - DD15", "Fuel Injectors", "Interstate McBee", 375.00, 649.95, 6, 150.00, "MCBEE"),
    ("PAI-351500", "Turbo Actuator - DD15", "Turbochargers", "PAI Industries", 285.00, 489.95, 3, 0.00, "PAI"),
    ("DORM-904-5010", "DPF Sensor Kit", "Emissions", "Dorman HD Solutions", 65.00, 119.95, 10, 0.00, "DORMAN"),
    ("DORM-904-7631", "DEF Injector", "Emissions", "Dorman HD Solutions", 125.00, 219.95, 8, 0.00, "DORMAN"),
]

with get_db() as conn:
    for sku, title, cat, mfg, cost, price, qty, core, vc in products_data:
        pv_id = vendor_ids.get(vc)
        conn.execute("""
            INSERT INTO products (sku, title, category, manufacturer,
                                 cost, selling_price, qty_on_hand,
                                 core_charge, preferred_vendor_id,
                                 reorder_point, min_qty, max_qty,
                                 created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (sku, title, cat, mfg, cost, price, qty, core, pv_id,
              max(2, qty // 3), max(2, qty // 3), qty * 2,
              now.isoformat(), now.isoformat()))
    conn.commit()
print(f"Products: {len(products_data)} created")

# ── Purchase Orders ───────────────────────────────────────────────
po_data = [
    ("PAI", [("PAI-681800E", 4, 185.00), ("PAI-451400", 12, 89.00), ("PAI-451505", 6, 175.00)], "received"),
    ("MCBEE", [("MCBEE-M-3803703", 6, 245.00), ("MCBEE-M-4928260", 4, 310.00)], "received"),
    ("CUMFILT", [("CUMF-LF9009", 24, 12.50), ("CUMF-FF5580", 24, 18.00), ("CUMF-AF25962", 10, 42.00)], "received"),
    ("DORMAN", [("DORM-904-5004", 2, 285.00), ("DORM-904-7631", 4, 125.00)], "ordered"),
    ("MAXI", [("MAXI-2250187", 1, 2100.00)], "ordered"),
]

with get_db() as conn:
    for vc, lines, status in po_data:
        vid = vendor_ids[vc]
        po_date = (now - timedelta(days=14)).strftime("%Y-%m-%d")
        expected = (now + timedelta(days=7)).strftime("%Y-%m-%d")

        result = create_purchase_order(
            vendor_id=vid, order_date=po_date,
            expected_date=expected, notes=f"Test PO from {vc}")
        po_id = result["po_id"]

        for sku, qty, unit_cost in lines:
            prod = conn.execute("SELECT id FROM products WHERE sku=%s", (sku,)).fetchone()
            if prod:
                add_po_line(po_id=po_id, product_id=prod["id"],
                           quantity_ordered=qty, unit_cost=unit_cost)

        if status != "draft":
            update_po_status(po_id, status)
print(f"Purchase Orders: {len(po_data)} created")

# ── Invoices ──────────────────────────────────────────────────────
invoice_data = [
    # (customer, days_ago, status, lines: [(sku, qty, price)])
    ("Southwest Freight Lines", 45, "paid", [
        ("CUMF-LF9009", 6, 24.95), ("CUMF-FF5580", 6, 34.95),
        ("CUMF-AF25962", 2, 79.95),
    ]),
    ("Southwest Freight Lines", 20, "sent", [
        ("PAI-681800E", 1, 329.95), ("NTP-550320", 1, 54.95),
    ]),
    ("Desert Haulers Inc", 30, "paid", [
        ("MCBEE-M-3803703", 6, 449.95),
    ]),
    ("Desert Haulers Inc", 10, "sent", [
        ("PAI-451400", 6, 159.95), ("PAI-451505", 6, 299.95),
        ("MAXI-2251117", 1, 479.95),
    ]),
    ("Henderson Fleet Services", 60, "paid", [
        ("PAI-060037", 1, 359.95), ("CUMF-LF9009", 12, 24.95),
    ]),
    ("Henderson Fleet Services", 15, "sent", [
        ("DORM-904-5004", 1, 489.95), ("DORM-904-5002", 1, 699.95),
    ]),
    ("Pacific Coast Carriers", 35, "paid", [
        ("MAXI-2250125", 1, 2899.95),
    ]),
    ("Mojave Express", 25, "partial", [
        ("BORG-179514", 1, 1899.95), ("PAI-350620", 1, 749.95),
    ]),
    ("Great Basin Transport", 40, "paid", [
        ("FPD-1830629C93", 4, 349.95), ("IPD-1842570C96", 1, 219.95),
    ]),
    ("Kingman Diesel Repair", 5, "sent", [
        ("PAI-321325", 1, 349.95), ("PAI-321402", 1, 379.95),
    ]),
    ("Valley Concrete Pumping", 55, "paid", [
        ("NTP-550210", 2, 479.95), ("NTP-550320", 2, 54.95),
    ]),
    ("Mesquite Trucking Co", 18, "sent", [
        ("MCBEE-M-4928260", 2, 549.95), ("CUMF-LF9009", 6, 24.95),
    ]),
    ("Apex Waste Haulers", 50, "paid", [
        ("PAI-136085", 1, 699.95), ("PAI-050090", 2, 259.95),
    ]),
    ("Cash Customer", 3, "paid", [
        ("CUMF-LF9009", 2, 24.95), ("CUMF-FF5580", 2, 34.95),
    ]),
    ("Arizona Heavy Haul", 28, "sent", [
        ("DORM-904-7631", 2, 219.95), ("DORM-904-5010", 2, 119.95),
    ]),
    ("Silver State Trucking", 38, "paid", [
        ("PAI-681806", 1, 349.95), ("MCBEE-M-R631416", 2, 649.95),
    ]),
    ("Sierra Pacific Fleet", 12, "sent", [
        ("PAI-351500", 1, 489.95), ("MAXI-2251302", 4, 42.95),
    ]),
    ("Zion Logistics", 22, "sent", [
        ("PAI-480200", 1, 289.95), ("PAI-050105", 4, 149.95),
    ]),
    ("Southwest Freight Lines", 8, "draft", [
        ("PAI-060101", 1, 399.95), ("CUMF-CV52001", 2, 64.95),
    ]),
    ("Desert Haulers Inc", 2, "draft", [
        ("NTP-601100", 1, 749.95),
    ]),
]

with get_db() as conn:
    for cust_name, days_ago, status, lines in invoice_data:
        cid = cust_ids[cust_name]
        inv_date = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        due_date = (now - timedelta(days=days_ago) + timedelta(days=30)).strftime("%Y-%m-%d")

        result = create_invoice(
            customer_id=cid,
            invoice_date=inv_date, due_date=due_date,
            notes="Thank you for your business!")
        inv_id = result["id"]

        for sku, qty, price in lines:
            prod = conn.execute("SELECT id, title FROM products WHERE sku=%s", (sku,)).fetchone()
            if prod:
                add_invoice_line(
                    invoice_id=inv_id, product_id=prod["id"],
                    sku=sku, description=prod["title"],
                    quantity=qty, unit_price=price)

        if status != "draft":
            finalize_invoice(inv_id)
            if status == "paid":
                inv = conn.execute("SELECT total FROM invoices WHERE id=%s", (inv_id,)).fetchone()
                if inv:
                    record_payment(inv_id, float(inv["total"]), "check")
            elif status == "partial":
                inv = conn.execute("SELECT total FROM invoices WHERE id=%s", (inv_id,)).fetchone()
                if inv:
                    record_payment(inv_id, float(inv["total"]) * 0.5, "check")

print(f"Invoices: {len(invoice_data)} created")

# ── Summary ───────────────────────────────────────────────────────
with get_db() as conn:
    stats = {
        "Products": conn.execute("SELECT COUNT(*) as c FROM products").fetchone()["c"],
        "Customers": conn.execute("SELECT COUNT(*) as c FROM customers").fetchone()["c"],
        "Vendors": conn.execute("SELECT COUNT(*) as c FROM vendors").fetchone()["c"],
        "Invoices": conn.execute("SELECT COUNT(*) as c FROM invoices").fetchone()["c"],
        "Invoice Lines": conn.execute("SELECT COUNT(*) as c FROM invoice_lines").fetchone()["c"],
        "Purchase Orders": conn.execute("SELECT COUNT(*) as c FROM purchase_orders").fetchone()["c"],
        "Locations": conn.execute("SELECT COUNT(*) as c FROM storage_locations").fetchone()["c"],
    }

print("\n=== TEST DATABASE READY ===")
for k, v in stats.items():
    print(f"  {k}: {v}")
print("===========================")
