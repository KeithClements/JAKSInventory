from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import app.database as _appdb
from app.database import init_db
from app.routers.settings import seed_settings
from app.seeds import (
    seed_default_categories, seed_customer_type_defaults,
    seed_brands, seed_manufacturers, backfill_category_keywords,
    seed_company_locations,
)
from app.routers.settings import seed_markup_tiers
from app.routers import (
    dashboard,
    products,
    categories as categories_router,
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
from app.routers import vendor_returns as vendor_returns_router
from app.routers import admin as admin_router
from app.routers import line_items as line_items_router
from app.routers import backup as backup_router
from app.routers import auth as auth_router
from app.routers import activities as activities_router
from app.routers import demo as demo_router
from app.routers import qbo as qbo_router
from app.routers import import_review as import_review_router
from app.routers import credit_memos as credit_memos_router
from app.routers import shopify as shopify_router
from app.routers import leadfinder_api as leadfinder_api_router
from app.routers import public_docs as public_docs_router
from app.routers import pricing_rules as pricing_rules_router
from app.routers import serials as serials_router
from app.routers import catalog_hygiene as catalog_hygiene_router

log = logging.getLogger(__name__)

app = FastAPI(title="JAKS Inventory", docs_url=None, redoc_url=None)

BASE_DIR = Path(__file__).resolve().parent
# §21 — self-hosted fonts: register the woff2 MIME so StaticFiles serves them as
# font/woff2 (Python's mimetypes doesn't know .woff2 by default).
import mimetypes
mimetypes.add_type("font/woff2", ".woff2")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# ── C5 — persistent file logging (FULL_ERP_REVIEW_2026-07-04) ──────────────────
# The real launcher runs `uvicorn app.main:app` (not run.py), so INFO logs from
# app.* used to hit Python's last-resort handler and vanish (WARNING+ died with the
# console window). A 2am failure was undiagnosable the next day. Configure a
# rotating file handler here so it applies regardless of launch method. Skipped on
# the in-memory test engine so the suite never writes log files.
LOG_DIR = BASE_DIR.parent / "logs"


def _configure_file_logging() -> None:
    """Attach a rotating file handler (INFO+) to the root logger, once. No-op on
    the in-memory test engine, and idempotent across repeated startup events."""
    if ":memory:" in str(_appdb.engine.url):
        return
    root = logging.getLogger()
    log_path = LOG_DIR / "axle.log"
    if any(
        isinstance(h, logging.FileHandler)
        and getattr(h, "baseFilename", "") == str(log_path)
        for h in root.handlers
    ):
        return  # already configured (repeated startup)
    try:
        from logging.handlers import RotatingFileHandler
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
        ))
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        root.addHandler(handler)
        log.info("file logging → %s", log_path)
    except Exception:  # noqa: BLE001 — logging setup must never block boot
        log.exception("could not configure file logging (continuing, console only)")


# ── C5 — health check for an external watchdog/monitor ─────────────────────────
# GET /health (auth-exempt, see _AUTH_EXEMPT) gives a monitor something to poll.
# Returns 200 when the DB answers, 503 when it doesn't (so a watchdog goes red),
# plus best-effort freshness signals the owner can alert on. Nothing here may raise.
@app.get("/health")
def health():
    from fastapi.responses import JSONResponse
    from sqlalchemy import text as _sql_text

    result: dict = {"status": "ok", "time": datetime.now().isoformat()}

    try:
        db = _appdb.SessionLocal()
        try:
            db.execute(_sql_text("SELECT 1"))
            result["db"] = "ok"
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        result["db"] = "error"
        result["status"] = "degraded"

    # Best-effort freshness: backup + shopify sync age. Never fail health on these;
    # surface them so an external check can alert on staleness.
    try:
        from app.settings_utils import get_setting_value
        for key in ("backup_last_run", "shopify_last_sync"):
            raw = (get_setting_value(key, "") or "").strip()
            if not raw:
                result[key] = None
                continue
            result[key] = raw
            try:
                ts = datetime.fromisoformat(raw)
                result[key.replace("last_run", "age_hours").replace("last_sync", "sync_age_hours")] = (
                    round((datetime.now() - ts).total_seconds() / 3600, 1)
                )
            except ValueError:
                pass
    except Exception:  # noqa: BLE001
        pass

    return JSONResponse(result, status_code=200 if result["status"] == "ok" else 503)


# ── Auth enforcement (O2 — "A: enforce") ──────────────────────────────────────
# Redirects every unauthenticated request to /login.
# Set JAKS_SKIP_AUTH=1 to bypass (used by the test suite — tests are not
# testing auth enforcement, they test business logic). R1-15: the bypass is
# honored ONLY when the active engine is in-memory (deps._is_test_env) so a
# stray JAKS_SKIP_AUTH in a production env can never disable auth on the file DB.
_AUTH_EXEMPT = frozenset({"/login", "/logout", "/health"})

@app.middleware("http")
async def enforce_login(request: Request, call_next):
    if os.getenv("JAKS_SKIP_AUTH"):
        from app.deps import _is_test_env
        if _is_test_env():                             # test bypass — in-memory engine only
            return await call_next(request)
    path = request.url.path
    if (path in _AUTH_EXEMPT or path.startswith("/static/")
            or path.startswith("/api/leadfinder") or path.startswith("/p/")):
        # /p/ = §22.7 public signed document links (the token is the credential).
        return await call_next(request)
    from app.auth import SESSION_COOKIE, read_session_token
    if read_session_token(request.cookies.get(SESSION_COOKIE)) is None:
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# ── §23 / Lane G — Default-password rotation gate (Risk #8/#14) ────────────────
# A non-dismissable redirect gate: while a signed-in user's account STILL verifies
# its well-known default password (admin/admin or bookkeeper/bookkeeper), every
# authenticated navigation is forced to the change-password screen (/account) until
# the password is rotated. Once changed, normal navigation resumes automatically.
#
# This replaces the old log-only warning (_warn_if_default_admin_password) with an
# enforced gate so a pre-employee / internet-adjacent instance can never be left
# wide open just because the launch banner was ignored. Honors the same test bypass
# as enforce_login (JAKS_SKIP_AUTH + in-memory engine only).
#
# Default credential map: username → its shipped default password. Mirrors the seed
# defaults in _seed_default_user (JAKS_ADMIN_PASSWORD / JAKS_BOOKKEEPER_PASSWORD
# fall back to "admin" / "bookkeeper").
_DEFAULT_CREDENTIALS = {"admin": "admin", "bookkeeper": "bookkeeper"}

# Paths the gated user MUST still reach so they can actually rotate the password
# (the change-password form, its POST handler, and logout). Static assets too so
# the /account page renders. Everything else redirects to /account.
_PW_GATE_EXEMPT = frozenset({"/account", "/account/password", "/logout", "/login", "/health"})


def account_uses_default_password(user) -> bool:
    """True if ``user``'s stored hash still verifies its shipped default password."""
    from app.auth import verify_password
    default_pw = _DEFAULT_CREDENTIALS.get((user.username or "").strip().lower())
    if default_pw is None:
        return False
    return verify_password(default_pw, user.password_hash)


@app.middleware("http")
async def enforce_password_rotation(request: Request, call_next):
    # Test bypass — identical contract to enforce_login (in-memory engine only) so
    # the business-logic suite is unaffected; the gate has its own dedicated test
    # that un-sets JAKS_SKIP_AUTH to exercise the real path.
    if os.getenv("JAKS_SKIP_AUTH"):
        from app.deps import _is_test_env
        if _is_test_env():
            return await call_next(request)

    path = request.url.path
    if (path in _PW_GATE_EXEMPT or path.startswith("/static/")
            or path.startswith("/api/leadfinder") or path.startswith("/p/")):
        return await call_next(request)

    from app.auth import SESSION_COOKIE, read_session_token
    uid = read_session_token(request.cookies.get(SESSION_COOKIE))
    if uid is None:
        # Unauthenticated — enforce_login (inner) handles the /login redirect.
        return await call_next(request)

    db = _appdb.SessionLocal()
    try:
        from app.models.user import User
        user = db.query(User).filter(User.id == uid).first()
        if user is not None and account_uses_default_password(user):
            # HTMX partials need HX-Redirect so the browser navigates rather than
            # swapping the /account page into a fragment slot.
            if request.headers.get("HX-Request"):
                from fastapi.responses import Response
                return Response(status_code=200, headers={"HX-Redirect": "/account"})
            return RedirectResponse("/account", status_code=303)
    finally:
        db.close()
    return await call_next(request)


# ── §21.3 Internet-exposed hardening (owner decision 6.16 #5) ──────────────────
# Registered AFTER enforce_login so, by middleware stack order, security headers
# is OUTERMOST (stamps every response incl. redirects/403s) and CSRF sits OUTSIDE
# auth (issues its cookie even on the unauthenticated /login redirect). Both honor
# the test bypass (JAKS_SKIP_AUTH + in-memory engine).
from app.security import (  # noqa: E402
    CSRFMiddleware,
    security_headers_middleware,
    resolve_current_user,
)
app.add_middleware(CSRFMiddleware)
app.middleware("http")(security_headers_middleware)
# Resolve request.state.current_user from the session cookie so base.html can
# render the real signed-in user's initials + Account / Sign-out menu. Registered
# last → runs INNERMOST of the @middleware("http") stack, so state is populated
# before the route/template runs, and AFTER enforce_login has admitted the request.
app.middleware("http")(resolve_current_user)


@app.on_event("startup")
def on_startup() -> None:
    _configure_file_logging()
    init_db()
    # Late-bound so tests that re-point app.database.SessionLocal at an isolated
    # engine (see tests/conftest.py) have startup seed THAT engine, not the file DB.
    db = _appdb.SessionLocal()
    try:
        seed_settings(db)
        _ensure_search_norm_columns(db)
        seed_default_categories(db)
        backfill_category_keywords(db)
        seed_brands(db)
        seed_manufacturers(db)
        seed_company_locations(db)
        seed_customer_type_defaults(db)
        seed_markup_tiers(db)
        _seed_session_secret(db)
        _seed_default_user(db)
        _warn_if_default_admin_password(db)
        _seed_core_locations(db)
        _lock_overdue_invoices(db)
        _scan_overdue_cores(db)
        _startup_backup(db)
    finally:
        db.close()
    # Background daemon schedulers must NEVER run under the test suite. Each helper
    # self-skips an in-memory engine, but a file-DB test (backup/restore, alembic
    # adoption) legitimately points the app at a temp FILE engine — so that guard
    # doesn't fire, the daemon threads spawn, and they persist for the rest of the
    # session ticking on the shared global SessionLocal against whatever in-memory
    # engine a LATER test activated. That concurrently uses the later test's single
    # StaticPool connection and corrupts its in-flight transaction — the true source
    # of the "different unrelated test fails each run" flakiness (mis-attributed to
    # hash-order). conftest sets JAKS_DISABLE_SCHEDULERS=1 so every test is covered,
    # file-DB ones included; production never sets it, so the schedulers run normally.
    if os.environ.get("JAKS_DISABLE_SCHEDULERS") == "1":
        return
    _start_shopify_scheduler()
    _start_shopify_order_poll()
    _start_shopify_weekly_audit()
    _start_overdue_core_scheduler()
    _start_qbo_retry_scheduler()
    _start_inventory_resync_scheduler()


def _ensure_search_norm_columns(db: Session) -> None:
    """Idempotently add + backfill + auto-maintain the precomputed normalized
    part-number search columns (fast list/workspace part-number search). Skipped
    on non-SQLite; never blocks startup."""
    try:
        from app.services.search_index import ensure_search_norm_columns
        if db.bind is None or db.bind.dialect.name != "sqlite":
            return
        ensure_search_norm_columns(db.connection())
        db.commit()
    except Exception:  # noqa: BLE001 — search index must never block boot
        db.rollback()
        import logging
        logging.getLogger(__name__).exception("search norm-column setup failed")


def _startup_backup(db: Session) -> None:
    """
    O3 — best-effort automatic SQLite backup on startup.

    Skipped under tests / in-memory engines (nothing on disk to snapshot) and
    throttled by ``backup_min_interval_hours`` so frequent restarts don't spam
    the backup dir.  Never raises — a backup failure must not block startup.
    """
    from app.settings_utils import get_setting_value_db, set_setting_value_db
    from app.services import backup_service

    try:
        # In-memory test engine → no file DB to back up.
        if ":memory:" in str(_appdb.engine.url):
            return
        if get_setting_value_db(db, "backup_on_startup", "true").strip().lower() != "true":
            return

        # Throttle: skip if the last backup is newer than the configured interval.
        last = get_setting_value_db(db, "backup_last_run", "")
        try:
            min_hours = float(get_setting_value_db(db, "backup_min_interval_hours", "12"))
        except (TypeError, ValueError):
            min_hours = 12.0
        if last and min_hours > 0:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
                if elapsed < min_hours * 3600:
                    return
            except ValueError:
                pass  # unparseable timestamp — fall through and take a backup

        backup_service.create_backup()
        backup_service.prune_backups()
        backup_service.offsite_copy()  # best-effort; no-op unless backup_offsite_dir is set
        set_setting_value_db(db, "backup_last_run", datetime.now().isoformat(timespec="seconds"))
        db.commit()
        log.info("startup backup complete")
    except Exception:
        log.exception("startup backup failed (continuing startup)")


def _start_shopify_scheduler() -> None:
    """Nightly price + stock sync to the linked Shopify storefront.

    OFF by default (set shopify_auto_sync_enabled='1' in Settings → Shopify to turn
    on). A daemon thread re-reads the setting each cycle so toggling takes effect
    without a restart, and fires once per day when the local hour matches
    shopify_auto_sync_hour. Skipped under the in-memory test engine (mirrors
    _startup_backup); never raises into startup."""
    import threading
    import time as _time

    if ":memory:" in str(_appdb.engine.url):
        return

    def _loop() -> None:
        from app.settings_utils import get_setting_value_db
        from app.services.shopify_service import run_background_shopify_sync
        last_run_date = None
        while True:
            try:
                db = _appdb.SessionLocal()
                try:
                    enabled = get_setting_value_db(
                        db, "shopify_auto_sync_enabled", "0").strip() == "1"
                    try:
                        hour = int(get_setting_value_db(db, "shopify_auto_sync_hour", "2"))
                    except (TypeError, ValueError):
                        hour = 2
                finally:
                    db.close()
                now = datetime.now()
                if enabled and now.hour == hour and last_run_date != now.date():
                    last_run_date = now.date()
                    log.info("shopify nightly sync starting")
                    run_background_shopify_sync(None)   # system run; opens own session
                    log.info("shopify nightly sync finished")
            except Exception:
                log.exception("shopify nightly scheduler tick failed (continuing)")
            _time.sleep(600)   # re-check every 10 minutes

    threading.Thread(target=_loop, daemon=True, name="shopify-nightly-sync").start()
    log.info("shopify nightly scheduler started (idle until enabled in Settings)")


def _start_shopify_order_poll() -> None:
    """Poll Shopify orders → decrement ERP stock (the inbound half of the sync).

    OFF by default (set shopify_order_poll_enabled='1' in Settings → Shopify).
    Cadence = shopify_order_poll_interval_min (default 5). A daemon thread re-reads
    both settings each cycle so toggling takes effect without a restart. Skipped
    under the in-memory test engine (mirrors _start_shopify_scheduler); never raises
    into startup."""
    import threading
    import time as _time

    if ":memory:" in str(_appdb.engine.url):
        return

    def _loop() -> None:
        from app.settings_utils import get_setting_value_db
        from app.services.shopify_order_sync import run_order_poll
        last = 0.0
        while True:
            try:
                db = _appdb.SessionLocal()
                try:
                    enabled = get_setting_value_db(
                        db, "shopify_order_poll_enabled", "0").strip() == "1"
                    try:
                        interval = max(1, int(get_setting_value_db(
                            db, "shopify_order_poll_interval_min", "5")))
                    except (TypeError, ValueError):
                        interval = 5
                finally:
                    db.close()
                now = _time.monotonic()
                if enabled and (now - last) >= interval * 60:
                    last = now
                    log.info("shopify order poll starting")
                    run_order_poll(None)   # system run; opens own session
                    log.info("shopify order poll finished")
            except Exception:
                log.exception("shopify order poll tick failed (continuing)")
            _time.sleep(60)   # re-check every minute

    threading.Thread(target=_loop, daemon=True, name="shopify-order-poll").start()
    log.info("shopify order poll scheduler started (idle until enabled in Settings)")


def _start_shopify_weekly_audit() -> None:
    """Weekly live-status refresh + full-catalog reconcile so the stale-cache
    backlog (vendor-OOS parts silently left live) can't reaccumulate, plus the
    locked-price margin alert. OFF by default (shopify_weekly_audit_enabled='1').
    Runs at most once per 7 days; skipped under the in-memory test engine."""
    import threading
    import time as _time

    if ":memory:" in str(_appdb.engine.url):
        return

    def _loop() -> None:
        from app.settings_utils import get_setting_value_db
        while True:
            try:
                db = _appdb.SessionLocal()
                try:
                    enabled = get_setting_value_db(
                        db, "shopify_weekly_audit_enabled", "0").strip() == "1"
                    last = get_setting_value_db(db, "shopify_weekly_audit_last", "")
                finally:
                    db.close()
                due = True
                if last:
                    try:
                        due = (datetime.now() - datetime.fromisoformat(last)
                               ).total_seconds() >= 7 * 86400
                    except ValueError:
                        due = True
                if enabled and due:
                    from app.services.shopify_service import run_weekly_audit
                    log.info("shopify weekly audit starting")
                    run_weekly_audit(None)   # system run; opens own session
                    log.info("shopify weekly audit finished")
            except Exception:
                log.exception("shopify weekly audit tick failed (continuing)")
            _time.sleep(3600)   # check hourly

    threading.Thread(target=_loop, daemon=True, name="shopify-weekly-audit").start()
    log.info("shopify weekly audit scheduler started (idle until enabled in Settings)")


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


def _seed_session_secret(db: Session) -> None:
    """
    O2 — ensure a random session-signing secret exists so login cookies survive
    restarts. Generated once; never regenerated (that would invalidate sessions).
    """
    import os
    from app.settings_utils import get_setting_value_db, set_setting_value_db

    if not get_setting_value_db(db, "session_secret_key", ""):
        set_setting_value_db(
            db, "session_secret_key", os.urandom(32).hex(),
            label="Signing key for login session cookies (auto-generated)",
        )
        db.commit()


def _seed_default_user(db: Session) -> None:
    """
    Ensure the single admin user (id=1) exists so audit log foreign keys and auth
    queries always have a valid row to join against.

    O2 — the admin password is now a REAL pbkdf2 hash (was a non-functional
    placeholder). Initial password comes from the JAKS_ADMIN_PASSWORD env var,
    defaulting to "admin" for first local run (change it after first login). An
    existing user #1 still carrying the old placeholder hash is upgraded in place
    so pre-O2 databases get a working login without a manual reset.
    """
    import os
    from app.auth import hash_password
    from app.models.user import User
    from app.constants import UserRole

    initial_pw = os.environ.get("JAKS_ADMIN_PASSWORD", "admin")
    user = db.query(User).filter(User.id == 1).first()
    if user is None:
        db.add(User(
            id=1,
            name="JAKS Admin",
            username="admin",
            password_hash=hash_password(initial_pw),
            role=UserRole.ADMIN,
        ))
        db.commit()
    elif not (user.password_hash or "").startswith("pbkdf2_"):
        # Upgrade a legacy placeholder hash so login works on pre-O2 DBs.
        user.password_hash = hash_password(initial_pw)
        db.commit()

    # O2 — also seed the second operator (bookkeeping / "the wife") so she has an
    # attributed, NON-admin login out of the box (satisfies §11 "second user").
    # Password from JAKS_BOOKKEEPER_PASSWORD (default "bookkeeper"); change after
    # first login. Idempotent: skipped if any bookkeeping user already exists or
    # the username is taken, so a real account the owner creates later wins.
    bk_username = os.environ.get("JAKS_BOOKKEEPER_USERNAME", "bookkeeper")
    bk_pw = os.environ.get("JAKS_BOOKKEEPER_PASSWORD", "bookkeeper")
    already_bookkeeper = db.query(User).filter(User.role == UserRole.BOOKKEEPING).first()
    username_taken = db.query(User).filter(User.username == bk_username).first()
    if already_bookkeeper is None and username_taken is None:
        db.add(User(
            name="JAKS Bookkeeping",
            username=bk_username,
            password_hash=hash_password(bk_pw),
            role=UserRole.BOOKKEEPING,
        ))
        db.commit()


def _warn_if_default_admin_password(db: Session) -> None:
    """O2 hardening — log a loud SECURITY warning if the admin account still uses
    the well-known default 'admin' password, so a production instance is never
    unknowingly left wide open. Skipped for in-memory test engines. The owner
    changes it at /account after signing in, or sets JAKS_ADMIN_PASSWORD before
    first run.

    Lane G (§23) promoted this from a log-only warning into an *enforced* gate:
    the ``enforce_password_rotation`` middleware now redirects any signed-in user
    whose account still verifies a default password to /account until it is
    rotated. This log line remains for ops visibility at startup.
    """
    import logging
    from app.auth import verify_password
    from app.models.user import User

    if ":memory:" in str(_appdb.engine.url):
        return
    admin = db.query(User).filter(User.username == "admin").first()
    if admin is not None and verify_password("admin", admin.password_hash):
        logging.getLogger("app.security").warning(
            "SECURITY: the 'admin' account is using the DEFAULT password. Set the "
            "JAKS_ADMIN_PASSWORD env var before go-live, or change it at /account "
            "after signing in. (This warning persists until the password is changed.)"
        )


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


def _scan_overdue_cores(db: Session) -> None:
    """
    R1-9 — run the overdue-core scan on startup so core_overdue notifications
    actually fire (CoreService.mark_overdue_cores previously had no caller).
    Never raises — a scan failure must not block startup.
    """
    from app.services.core_service import CoreService

    try:
        # current_user_id=None → "system event" semantics (see BaseService).
        counts = CoreService(db, current_user_id=None).mark_overdue_cores()
        if counts.get("overdue") or counts.get("approaching"):
            log.info("startup overdue-core scan: %s", counts)
    except Exception:
        log.exception("startup overdue-core scan failed (continuing startup)")


def _start_overdue_core_scheduler() -> None:
    """§21 — DAILY overdue-core scan. The startup scan above only fires once; a
    server left up for days would never re-flag cores whose return deadline has
    since passed, so outstanding core liability (hundreds per reman head) goes
    uncollected. A daemon thread re-scans once per day (mirrors the Shopify
    scheduler). Skipped under the in-memory test engine; never raises."""
    import threading
    import time as _time

    if ":memory:" in str(_appdb.engine.url):
        return

    def _loop() -> None:
        last_run_date = None
        while True:
            try:
                now = datetime.now()
                # Fire once per day at/after 06:00 local.
                if now.hour >= 6 and last_run_date != now.date():
                    last_run_date = now.date()
                    db = _appdb.SessionLocal()
                    try:
                        _scan_overdue_cores(db)
                    finally:
                        db.close()
            except Exception:
                log.exception("daily overdue-core scheduler tick failed (continuing)")
            _time.sleep(1800)   # re-check every 30 minutes

    threading.Thread(target=_loop, daemon=True, name="overdue-core-daily-scan").start()
    log.info("daily overdue-core scheduler started")


def _qbo_retry_tick() -> dict | None:
    """One pass of the QBO retry worker, extracted from the thread loop so tests
    can drive it directly. Re-pushes ERROR-status invoices, payments, vendor
    bills, and credit memos (each under its retry ceiling) as the system actor
    (current_user_id=None — passes the REPUSH_QBO gate by design) while QBO is
    connected. Returns the retry summary (None when disconnected/failed); never
    raises."""
    try:
        db = _appdb.SessionLocal()
        try:
            from app.services.qbo_service import QBOSyncService
            svc = QBOSyncService(db, current_user_id=None)
            if not svc.connection_summary().get("connected"):
                return None
            return svc.retry_failed_pushes()
        finally:
            db.close()
    except Exception:
        log.exception("QBO retry scheduler tick failed (continuing)")
        return None


def _start_qbo_retry_scheduler() -> None:
    """§21 — background retry for ERROR-status QBO pushes (invoices, payments,
    vendor bills, credit memos). "Sync Failed" documents previously sat until
    someone manually re-pushed; this re-attempts them (under the per-document
    retry ceiling) every ~30 min while QBO is connected. Each push is already
    fail-soft and never touches the money path. Skipped under the in-memory
    test engine; never raises."""
    import threading
    import time as _time

    if ":memory:" in str(_appdb.engine.url):
        return

    def _loop() -> None:
        while True:
            _qbo_retry_tick()
            _time.sleep(1800)   # every 30 minutes

    threading.Thread(target=_loop, daemon=True, name="qbo-retry-worker").start()
    log.info("QBO retry scheduler started (idle until connected)")


# Log the not-yet-shipped resync method exactly once (the scheduler ticks daily
# forever — a missing method must not spam the log or crash the thread).
_inventory_resync_missing_logged = False


def _run_inventory_resync(db: Session) -> dict | None:
    """One inventory-drift resync pass (extracted so tests can drive it without
    the thread). Calls InventoryService.resync_all_products() — shipping from a
    concurrent lane, so it is resolved defensively via getattr: missing → log
    once and idle, never crash. Returns its {'checked','drifted','fixed'}
    summary (None when unavailable/failed); never raises."""
    global _inventory_resync_missing_logged
    try:
        from app.services.inventory_service import InventoryService

        svc = InventoryService(db, current_user_id=None)
        resync = getattr(svc, "resync_all_products", None)
        if not callable(resync):
            if not _inventory_resync_missing_logged:
                _inventory_resync_missing_logged = True
                log.warning(
                    "InventoryService.resync_all_products is not available — "
                    "the daily inventory resync is idle until it ships")
            return None
        counts = resync() or {}
        if counts.get("drifted"):
            log.warning(
                "inventory resync: %s of %s product(s) had DRIFTED on-hand "
                "counts (%s fixed)",
                counts.get("drifted"), counts.get("checked"), counts.get("fixed"),
            )
        else:
            log.info("inventory resync clean: %s", counts)
        return counts
    except Exception:
        log.exception("inventory resync failed (continuing)")
        return None


def _start_inventory_resync_scheduler() -> None:
    """Daily ledger-vs-cache drift resync for product on-hand counts (mirrors
    _start_overdue_core_scheduler: daemon thread, once-per-day tick, in-memory
    test-engine skip, never raises into startup)."""
    import threading
    import time as _time

    if ":memory:" in str(_appdb.engine.url):
        return

    def _loop() -> None:
        last_run_date = None
        while True:
            try:
                now = datetime.now()
                # Fire once per day at/after 05:00 local (before business hours,
                # staggered off the 06:00 overdue-core scan).
                if now.hour >= 5 and last_run_date != now.date():
                    last_run_date = now.date()
                    db = _appdb.SessionLocal()
                    try:
                        _run_inventory_resync(db)
                    finally:
                        db.close()
            except Exception:
                log.exception("daily inventory-resync scheduler tick failed (continuing)")
            _time.sleep(1800)   # re-check every 30 minutes

    threading.Thread(target=_loop, daemon=True, name="inventory-resync-daily").start()
    log.info("daily inventory resync scheduler started")


app.include_router(dashboard.router)
app.include_router(search_router.router)
app.include_router(products.router)
app.include_router(categories_router.router)
app.include_router(customers.router)
app.include_router(vendors.router)
app.include_router(purchase_orders.router)
app.include_router(quotes.router)
app.include_router(sales_orders.router)
app.include_router(invoices.router)
app.include_router(payments.router)
app.include_router(cores.router)
app.include_router(serials_router.router)
app.include_router(settings_router.router)
app.include_router(warranty_router.router)
app.include_router(returns_router.router)
app.include_router(reports_router.router)
app.include_router(notifications_router.router)
app.include_router(vendor_returns_router.router)
app.include_router(admin_router.router)
app.include_router(line_items_router.router)
app.include_router(backup_router.router)
app.include_router(auth_router.router)
app.include_router(activities_router.router)
app.include_router(demo_router.router)
app.include_router(qbo_router.router)
app.include_router(import_review_router.router)
app.include_router(credit_memos_router.router)
app.include_router(shopify_router.router)
app.include_router(leadfinder_api_router.router)
app.include_router(public_docs_router.router)
app.include_router(pricing_rules_router.router)
app.include_router(catalog_hygiene_router.router)


# ── Friendly 404 for browsers (QA — raw {"detail":"Not Found"} JSON) ───────────
# A missing page used to return raw JSON with a blank <title>. For a request that
# accepts HTML (a real browser navigation) render a branded page that extends
# base.html and links back to the dashboard; for API / fetch / HTMX requests keep
# the existing JSON body so programmatic callers are unaffected. Other HTTP errors
# fall through to FastAPI's default handler unchanged.
from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: E402
from fastapi.exception_handlers import (  # noqa: E402
    http_exception_handler as _default_http_exception_handler,
)

_error_templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


_HTML_ERROR_TEMPLATES = {404: "errors/404.html", 403: "errors/403.html"}


@app.exception_handler(_StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: _StarletteHTTPException):
    wants_html = "text/html" in request.headers.get("accept", "")
    is_htmx = request.headers.get("HX-Request") is not None
    template = _HTML_ERROR_TEMPLATES.get(exc.status_code)
    # 404 → "page doesn't exist"; 403 → a branded "access restricted" page that
    # extends base.html so a role-denied user (e.g. SALES landing on the
    # admin-only dashboard right after login) gets the nav instead of a raw JSON
    # blob with no way forward. Everything else keeps the JSON body.
    if template and wants_html and not is_htmx:
        # current_user may not be resolved (the user-resolution middleware runs
        # outside the exception-handler boundary on some error paths), so make it
        # safe for base.html's avatar block.
        if getattr(request.state, "current_user", None) is None:
            request.state.current_user = None
        return _error_templates.TemplateResponse(
            request, template, status_code=exc.status_code,
            context={"detail": getattr(exc, "detail", "")},
        )
    # Everything else → FastAPI's default JSON handler (unchanged behavior).
    return await _default_http_exception_handler(request, exc)
