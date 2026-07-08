"""
app/seeds.py
============
Reference data seeded on every startup.
Each function is idempotent — checks for existing rows before inserting.
Called from app/main.py on_startup after init_db().

Unlike settings (user-editable key/value pairs), seed data is structured
reference data that the application logic depends on.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.product import ProductCategory, Brand, Manufacturer
from app.constants import BRANDS, ENGINE_MAKES


# NOTE: vendor scraper sources removed — all scraping/cataloging is external now
# (owner's standalone tool feeds Shopify, not the ERP; PHASE_2_PLAN.md §6 P2-D9).


# ── Product categories ──────────────────────────────────────────────────────────
# A starter Major Group → Category tree so the product Category dropdown is usable
# out of the box (a fresh DB has none, so the picker showed only "— None —").
# A full category-management UI is deferred — see the Category panel TODO in
# app/templates/products/new.html. Each child sits one level under its group.

# Major Group → [(Subcategory, SKU code, importer keywords)]. Keywords are
# lowercase substrings matched against the product TITLE by ClassificationService;
# the classifier picks the first matching (deepest) subcategory, so keep them
# specific. This is what lets a fresh import classify the PAI catalog by part type
# — gasket / injector / turbo / … → a real subcategory (needs_review=False) AND a
# meaningful SKU [CATEGORY] code (GSK / INJ / TURBO …) — instead of dumping 96% of
# parts into the generic "ENGINE PARTS" Type bucket.
_CATEGORY_TREE: dict[str, list[tuple[str, str, str]]] = {
    "Engine": [
        ("Cylinder Heads",       "HEAD", "cylinder head, loaded head, bare head, head assembly, head gasket"),
        ("Seals & Gaskets",      "GSK",  "gasket, seal, o-ring, o ring, oring, grommet, washer seal"),
        ("Pistons & Liners",     "PIS",  "piston, liner, sleeve, wrist pin, piston ring, ring set"),
        ("Bearings",             "BRG",  "bearing, bushing, thrust washer"),
        ("Valvetrain",           "VLV",  "valve, camshaft, cam follower, lifter, rocker, push rod, pushrod, tappet, valve guide, valve spring"),
        ("Crankshaft & Rods",    "CRK",  "crankshaft, connecting rod, conrod, crank gear, rod assembly, harmonic balancer, vibration damper"),
        ("Fasteners & Hardware", "FAS",  "screw, bolt, stud, nut, dowel, clamp, plug, fitting, retainer, snap ring"),
    ],
    "Fuel System": [
        ("Injectors",    "INJ",     "injector, nozzle, injector cup, injector sleeve"),
        ("Fuel Pumps",   "FUELPMP", "fuel pump, injection pump, transfer pump, lift pump, hpfp"),
        ("Fuel Filters", "FUELFLT", "fuel filter, fuel water separator"),
    ],
    "Air & Turbo": [
        ("Turbochargers",       "TURBO",  "turbo, turbocharger, cartridge, chra"),
        ("Manifolds",           "MAN",    "manifold"),
        ("Air Filters",         "AIRFLT", "air filter"),
        ("Intake & Charge Air", "INTAKE", "intake, charge air, intercooler, air cooler, egr cooler, egr valve"),
    ],
    "Cooling System": [
        ("Water Pumps",         "WPMP",  "water pump, coolant pump"),
        ("Radiators & Coolers", "RAD",   "radiator, coolant tube, coolant manifold"),
        ("Thermostats",         "THERM", "thermostat"),
    ],
    "Lubrication": [
        ("Oil Pumps",   "OILPMP", "oil pump"),
        ("Oil Coolers", "OILCLR", "oil cooler"),
        ("Oil Filters", "OILFLT", "oil filter, oil pan, dipstick"),
    ],
    "Electrical": [
        ("Starters & Alternators", "ELEC", "starter, alternator, solenoid"),
        ("Sensors & Switches",     "SENS", "sensor, switch, sender, gauge"),
        ("Wiring & Connectors",    "WIRE", "wiring, connector, harness, terminal"),
    ],
}


def seed_default_categories(db: Session) -> None:
    """
    Seed the starter Major Group → Subcategory tree, each subcategory carrying its
    SKU [CATEGORY] code + importer classification keywords, so a fresh database
    classifies the PAI catalog by part type out of the box.

    Idempotent by emptiness: if ANY category already exists this is a no-op, so a
    hand-curated taxonomy is never overwritten and restarts never duplicate rows.
    (Use backfill_category_keywords() to enrich an already-populated tree.)
    """
    if db.query(ProductCategory.id).first() is not None:
        return
    for major, subs in _CATEGORY_TREE.items():
        parent = ProductCategory(name=major, parent_id=None, level=1, is_active=True)
        db.add(parent)
        db.flush()  # assign parent.id before linking children
        for name, code, kws in subs:
            db.add(ProductCategory(
                name=name, parent_id=parent.id, level=2, is_active=True,
                code=code, import_keywords=kws,
            ))
    db.commit()


def backfill_category_keywords(db: Session) -> None:
    """Enrich EXISTING categories (matched by name) with the seed SKU code + import
    keywords wherever those fields are blank — so a DB that already has the older
    keyword-less category tree also gains title-based classification. Never clobbers
    an owner-set value and never adds nodes. Idempotent."""
    by_name = {c.name.strip().lower(): c for c in db.query(ProductCategory).all()}
    changed = False
    for subs in _CATEGORY_TREE.values():
        for name, code, kws in subs:
            c = by_name.get(name.strip().lower())
            if not c:
                continue
            if not (c.import_keywords or "").strip():
                c.import_keywords = kws
                changed = True
            if not (c.code or "").strip():
                c.code = code
                changed = True
    if changed:
        db.commit()


# ── Customer type-default profiles (Phase 2, P2-D1) ───────────────────────────────
# One editable customer_type_defaults row per CustomerType, from the in-code
# starting profiles. Idempotent — only fills missing types, never clobbers a row
# the owner has customised. resolve_defaults() falls back to the in-code profiles
# anyway, so this is a convenience that makes the rows visible/editable.

def seed_customer_type_defaults(db: Session) -> None:
    from app.services.customer_service import CustomerService
    CustomerService(db).ensure_type_defaults_seeded()


# ── Brands & Manufacturers (§18.2 — three-axis classification) ────────────────
# Brand (PAI / Interstate-McBee / SAMPA / JAK'S) and Manufacturer / Engine-Make
# (Cummins, CAT, …) are owner-maintained lists, DISTINCT from each other and from
# Vendor. Both idempotent by emptiness so a curated list is never overwritten.

def seed_brands(db: Session) -> None:
    if db.query(Brand.id).first() is not None:
        return
    for i, name in enumerate(BRANDS):
        db.add(Brand(
            name=name, sort_order=i, is_active=True,
            is_house_brand=name.upper().startswith("JAK"),
        ))
    db.commit()


def seed_manufacturers(db: Session) -> None:
    if db.query(Manufacturer.id).first() is not None:
        return
    i = 0
    for name in ENGINE_MAKES:
        if name.strip().lower() == "other":
            continue  # 'Other' is a UI free-text escape hatch, not a real row
        db.add(Manufacturer(name=name, sort_order=i, is_active=True))
        i += 1
    db.commit()


# ── Company locations (PO bill-to / ship-to address book) ─────────────────────
# Seed ONE primary location (the default "ship to me" / bill-to) from the
# company_* settings so a fresh DB has a usable ship-to. Idempotent by emptiness
# — never clobbers a hand-managed location list.

def seed_company_locations(db: Session) -> None:
    from app.models.company_location import CompanyLocation
    from app.settings_utils import get_setting_value_db
    if db.query(CompanyLocation.id).first() is not None:
        return
    name = (get_setting_value_db(db, "company_name", "") or "").strip() or "Main Shop"
    db.add(CompanyLocation(
        name=name,
        address_line1=(get_setting_value_db(db, "company_address", "") or "").strip(),
        phone=(get_setting_value_db(db, "company_phone", "") or "").strip(),
        is_primary=True,
        is_active=True,
    ))
    db.commit()
