from __future__ import annotations

from datetime import datetime
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import init_db, SessionLocal
from app.routers.settings import seed_settings
from app.seeds import seed_scraper_sources
from app.routers import (
    dashboard,
    products,
    customers,
    vendors,
    purchase_orders,
    quotes,
    sales_orders,
    invoices,
    payments,
    cores,
    search as search_router,
    settings as settings_router,
)
from app.routers import warranty as warranty_router
from app.routers import returns as returns_router
from app.routers import reports as reports_router
from app.routers import notifications as notifications_router

app = FastAPI(title="JAKS Inventory", docs_url=None, redoc_url=None)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_settings(db)
        seed_scraper_sources(db)
        _seed_default_user(db)
        _seed_core_locations(db)
        _lock_overdue_invoices(db)
    finally:
        db.close()


def _seed_core_locations(db: Session) -> None:
    """
    R10 — seed the default core location buckets on startup.
    Idempotent: only inserts locations whose names don't already exist.
    """
    from app.models.core import CoreLocation

    existing = {row.name for row in db.query(CoreLocation.name).all()}
    defaults = [
        # name, description, vendor_id, is_in_transit, display_order
        ("Core Shelf",         "Default location for accepted cores",       None, False, 10),
        ("Core Holding",       "Held for review / decision pending",        None, False, 20),
        ("Questionable Core",  "Damaged / uncertain — needs decision",      None, False, 30),
        ("Rejected Core",      "Wrong core or rejected for credit",         None, False, 40),
        ("In Transit to Vendor", "Shipped to vendor — VCR open",            None, True,  50),
        ("Scrap Core",         "Scrapped / disposed",                       None, False, 60),
    ]
    for name, desc, vendor_id, in_transit, order in defaults:
        if name not in existing:
            db.add(CoreLocation(
                name=name,
                description=desc,
                vendor_id=vendor_id,
                is_in_transit=in_transit,
                display_order=order,
                is_active=True,
            ))
    db.commit()


def _seed_default_user(db: Session) -> None:
    """
    Ensure the single admin user (id=1) exists so audit log foreign keys
    and any future auth queries always have a valid row to join against.
    Password hash is a non-functional placeholder until auth is implemented.
    """
    from app.models.user import User
    from app.constants import UserRole
    if not db.query(User).filter(User.id == 1).first():
        db.add(User(
            id=1,
            name="JAKS Admin",
            username="admin",
            password_hash="[single-user-mode-no-auth]",
            role=UserRole.ADMIN,
        ))
        db.commit()


def _lock_overdue_invoices(db: Session) -> None:
    """
    On startup, lock any OPEN/PARTIAL invoices that are past their lock window.

    Lock criteria (any one triggers lock):
      - Invoice created on a previous calendar day (always lock — past EOD)
      - Invoice created today AND current local time >= business_close_time setting

    Invoices already locked (locked_at IS NOT NULL) are skipped.
    This is idempotent and safe to run multiple times.
    """
    from datetime import date, time as dt_time
    from app.constants import InvoiceStatus, InvoiceLockReason
    from app.models.invoice import Invoice
    from app.settings_utils import get_setting_value_db
    from app.services.invoice_service import InvoiceService

    lockable_statuses = {InvoiceStatus.OPEN, InvoiceStatus.PARTIAL}

    candidates = (
        db.query(Invoice)
        .filter(
            Invoice.status.in_(list(lockable_statuses)),
            Invoice.locked_at.is_(None),
        )
        .all()
    )
    if not candidates:
        return

    today = date.today()
    now = datetime.utcnow()

    # Parse business_close_time setting ("HH:MM", 24h, local time)
    close_str = get_setting_value_db(db, "business_close_time", "17:00")
    try:
        h, m = (int(x) for x in close_str.split(":"))
        close_time = dt_time(h, m)
    except (ValueError, AttributeError):
        close_time = dt_time(17, 0)

    current_local_time = now.time()  # server runs locally — UTC ≈ local for Keith

    svc = InvoiceService(db, current_user_id=1)

    for inv in candidates:
        created_date = inv.created_at.date() if inv.created_at else today

        if created_date < today:
            # Past a previous calendar day — lock unconditionally
            svc.lock(inv.id, reason=InvoiceLockReason.END_OF_DAY)
        elif created_date == today and current_local_time >= close_time:
            # Same day but EOD has passed
            svc.lock(inv.id, reason=InvoiceLockReason.END_OF_DAY)


app.include_router(dashboard.router)
app.include_router(search_router.router)
app.include_router(products.router)
app.include_router(customers.router)
app.include_router(vendors.router)
app.include_router(purchase_orders.router)
app.include_router(quotes.router)
app.include_router(sales_orders.router)
app.include_router(invoices.router)
app.include_router(payments.router)
app.include_router(cores.router)
app.include_router(settings_router.router)
app.include_router(warranty_router.router)
app.include_router(returns_router.router)
app.include_router(reports_router.router)
app.include_router(notifications_router.router)
