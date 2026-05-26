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
