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

from app.models.scraper import ScraperSource
from app.models.product import ProductCategory
from app.constants import ScraperSourceType


# ── Vendor scraper sources ─────────────────────────────────────────────────────
# Seeded inactive (is_active=False). VendorAvailabilityService returns
# "check manually" until the real scraper for that source is implemented.
# Activate each source by flipping is_active=True when its scraper is ready.

_VENDOR_SOURCES: list[dict] = [
    {
        "name": "PAI",
        "source_type": ScraperSourceType.VENDOR,
        "base_url": "https://www.paiindustries.com",
        "is_active": False,
        "requires_login": True,
        "notes": "Primary vendor. Login required for pricing. Phase 2 scraper.",
    },
    {
        "name": "HHP",
        "source_type": ScraperSourceType.VENDOR,
        "base_url": "",
        "is_active": False,
        "requires_login": False,
        "notes": "Heavy Hauler Parts. Phase 2 scraper.",
    },
    {
        "name": "ATL",
        "source_type": ScraperSourceType.VENDOR,
        "base_url": "",
        "is_active": False,
        "requires_login": False,
        "notes": "ATL Diesel. Phase 2 scraper.",
    },
]


def seed_scraper_sources(db: Session) -> None:
    """
    Insert any missing scraper source rows.
    Safe to run on every startup — skips rows that already exist by name.
    """
    existing_names = {row.name for row in db.query(ScraperSource.name).all()}
    new_rows = [
        ScraperSource(**src)
        for src in _VENDOR_SOURCES
        if src["name"] not in existing_names
    ]
    if new_rows:
        db.add_all(new_rows)
        db.commit()


# ── Product categories ──────────────────────────────────────────────────────────
# A starter Major Group → Category tree so the product Category dropdown is usable
# out of the box (a fresh DB has none, so the picker showed only "— None —").
# A full category-management UI is deferred — see the Category panel TODO in
# app/templates/products/new.html. Each child sits one level under its group.

_DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "Engine":         ["Seals & Gaskets", "Pistons & Liners", "Bearings", "Valvetrain"],
    "Fuel System":    ["Injectors", "Fuel Pumps", "Fuel Filters"],
    "Air & Turbo":    ["Turbochargers", "Air Filters", "Intake & Charge Air"],
    "Cooling System": ["Water Pumps", "Radiators & Coolers", "Thermostats"],
    "Lubrication":    ["Oil Pumps", "Oil Coolers", "Oil Filters"],
    "Electrical":     ["Starters & Alternators", "Sensors & Switches", "Wiring & Connectors"],
}


def seed_default_categories(db: Session) -> None:
    """
    Seed a starter category tree (Major Group → Category) so the product Category
    dropdown isn't empty on a fresh database.

    Idempotent by emptiness: if ANY category already exists this is a no-op, so a
    hand-curated taxonomy is never overwritten and restarts never duplicate rows.
    """
    if db.query(ProductCategory.id).first() is not None:
        return
    for major, subs in _DEFAULT_CATEGORIES.items():
        parent = ProductCategory(name=major, parent_id=None, level=1, is_active=True)
        db.add(parent)
        db.flush()  # assign parent.id before linking children
        for sub in subs:
            db.add(ProductCategory(
                name=sub, parent_id=parent.id, level=2, is_active=True,
            ))
    db.commit()


# ── Customer type-default profiles (Phase 2, P2-D1) ───────────────────────────────
# One editable customer_type_defaults row per CustomerType, from the in-code
# starting profiles. Idempotent — only fills missing types, never clobbers a row
# the owner has customised. resolve_defaults() falls back to the in-code profiles
# anyway, so this is a convenience that makes the rows visible/editable.

def seed_customer_type_defaults(db: Session) -> None:
    from app.services.customer_service import CustomerService
    CustomerService(db).ensure_type_defaults_seeded()
