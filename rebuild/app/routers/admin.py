"""
app/routers/admin.py
====================
Admin utilities. Currently hosts the smoke-test dashboard at /admin/smoke-tests,
which renders the latest results written by ``tests/smoke/runner.py`` and lets the
operator kick off a fresh run.

The actual Playwright run is heavy (it spins up its own isolated server + a real
browser), so it is NEVER run inside this request. The "Run" button launches the
runner as a DETACHED subprocess; the runner writes results JSON which this page
reads. While a run is in flight the page auto-refreshes.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.product import Product
from app.services.product_service import ProductService

# tests/ is importable from the repo root (the dev server runs from there).
from tests.smoke import report as smoke_report

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ── R3 — Inventory cache resync (ledger = source of truth) ────────────────────
# Product.qty_on_hand is a cache over the InventoryTransaction ledger. Bugs or
# ORM-bypassing writes can drift it with no recovery path short of a manual
# adjustment (which WRITES a ledger row, polluting history). These admin-only
# routes recompute the cache FROM the ledger and write it back — the corrective
# tool the audit found missing. Commitment rows (SO_COMMITTED / SO_RELEASED)
# are excluded by ProductService.get_qty_on_hand by design: committed stock is
# still physically on hand until the INVOICE_SALE row lands.

@router.post("/inventory/resync/{product_id}")
def inventory_resync_product(
    product_id: int,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """Recompute one product's qty_on_hand from the ledger and write the cache
    back. Returns old → new so the caller sees exactly what changed."""
    service = ProductService(db, current_user_id=admin.id)
    try:
        old_qty, new_qty = service.resync_qty_on_hand(product_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    db.commit()
    log.info(
        "Inventory resync: product %s qty_on_hand %s -> %s (delta %s) by user %s",
        product_id, old_qty, new_qty, new_qty - old_qty, admin.id,
    )
    return {
        "product_id": product_id,
        "old_qty_on_hand": old_qty,
        "new_qty_on_hand": new_qty,
        "delta": new_qty - old_qty,
    }


@router.post("/inventory/resync-all")
def inventory_resync_all(
    limit: int = 500,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """Recompute qty_on_hand from the ledger for up to ``limit`` active
    products (ordered by id, default 500 — a SQLite full-catalog pass over 13k
    parts is heavy enough to deserve explicit batching). Logs old → new per
    drifted product and returns a summary."""
    limit = max(1, min(int(limit), 5000))
    service = ProductService(db, current_user_id=admin.id)
    product_ids = [
        row[0]
        for row in (
            db.query(Product.id)
            .filter(Product.is_active == True)  # noqa: E712
            .order_by(Product.id)
            .limit(limit)
            .all()
        )
    ]
    corrected: list[dict] = []
    for pid in product_ids:
        old_qty, new_qty = service.resync_qty_on_hand(pid)
        if new_qty != old_qty:
            log.info(
                "Inventory resync-all: product %s qty_on_hand %s -> %s (delta %s)",
                pid, old_qty, new_qty, new_qty - old_qty,
            )
            corrected.append({
                "product_id": pid,
                "old_qty_on_hand": old_qty,
                "new_qty_on_hand": new_qty,
                "delta": new_qty - old_qty,
            })
    db.commit()
    log.info(
        "Inventory resync-all: checked %d product(s), corrected %d (limit %d) by user %s",
        len(product_ids), len(corrected), limit, admin.id,
    )
    return {
        "checked": len(product_ids),
        "corrected": len(corrected),
        "limit": limit,
        "corrections": corrected,
    }


# ── §21 — qty_committed / qty_on_order cache resync (recovery paths) ──────────
# qty_committed is a cache over the SO commitment ledger; qty_on_order a cache
# over open POs. Both could drift with no recovery before this (admin only had
# qty_on_hand resync). These mirror the resync-all pattern above.

@router.post("/inventory/resync-committed/{product_id}")
def inventory_resync_committed(
    product_id: int, db: Session = Depends(get_db), admin=Depends(require_admin),
):
    """Recompute one product's qty_committed from the commitment ledger."""
    old_qty, new_qty = ProductService(db, current_user_id=admin.id).resync_qty_committed(product_id)
    db.commit()
    log.info("qty_committed resync: product %s %s -> %s by user %s",
             product_id, old_qty, new_qty, admin.id)
    return {"product_id": product_id, "old_qty_committed": old_qty,
            "new_qty_committed": new_qty, "delta": new_qty - old_qty}


@router.post("/inventory/resync-on-order/{product_id}")
def inventory_resync_on_order(
    product_id: int, db: Session = Depends(get_db), admin=Depends(require_admin),
):
    """Recompute one product's qty_on_order from open (SENT/PARTIAL) POs."""
    old_qty, new_qty = ProductService(db, current_user_id=admin.id).resync_qty_on_order(product_id)
    db.commit()
    log.info("qty_on_order resync: product %s %s -> %s by user %s",
             product_id, old_qty, new_qty, admin.id)
    return {"product_id": product_id, "old_qty_on_order": old_qty,
            "new_qty_on_order": new_qty, "delta": new_qty - old_qty}


@router.post("/inventory/resync-availability-all")
def inventory_resync_availability_all(
    limit: int = 500, db: Session = Depends(get_db), admin=Depends(require_admin),
):
    """Recompute qty_committed AND qty_on_order for up to ``limit`` active
    products. Companion to resync-all (which covers qty_on_hand)."""
    limit = max(1, min(int(limit), 5000))
    service = ProductService(db, current_user_id=admin.id)
    product_ids = [
        row[0] for row in (
            db.query(Product.id).filter(Product.is_active == True)  # noqa: E712
            .order_by(Product.id).limit(limit).all()
        )
    ]
    corrected: list[dict] = []
    for pid in product_ids:
        oc, nc = service.resync_qty_committed(pid)
        oo, no = service.resync_qty_on_order(pid)
        if nc != oc or no != oo:
            corrected.append({"product_id": pid,
                              "qty_committed": [oc, nc], "qty_on_order": [oo, no]})
    db.commit()
    log.info("Availability resync-all: checked %d, corrected %d (limit %d) by user %s",
             len(product_ids), len(corrected), limit, admin.id)
    return {"checked": len(product_ids), "corrected": len(corrected),
            "limit": limit, "corrections": corrected}


@router.get("/smoke-tests", response_class=HTMLResponse)
def smoke_tests_dashboard(request: Request):
    """Render the smoke-test results dashboard."""
    results = smoke_report.read_results()
    running = smoke_report.is_running()
    return templates.TemplateResponse(
        request,
        "admin/smoke_tests.html",
        {
            "results": results,
            "running": running,
        },
    )


@router.post("/smoke-tests/run", response_class=RedirectResponse)
def smoke_tests_run():
    """Launch the smoke runner as a detached background process, then redirect
    back to the dashboard (which will auto-refresh while it runs)."""
    if smoke_report.is_running():
        return RedirectResponse("/admin/smoke-tests?msg=already-running", status_code=303)

    smoke_report.set_running(True)

    log_path = smoke_report.ARTIFACTS_DIR / "last_run.log"
    smoke_report.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "w", encoding="utf-8")

    creationflags = 0
    if sys.platform == "win32":
        # Detach so the run outlives this request and doesn't die with the worker.
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )

    try:
        subprocess.Popen(
            [sys.executable, "-m", "tests.smoke.runner"],
            cwd=str(_REPO_ROOT),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception:
        # If we couldn't even launch, clear the lock so the page isn't stuck.
        smoke_report.set_running(False)
        log_fh.close()
        return RedirectResponse("/admin/smoke-tests?msg=launch-failed", status_code=303)

    return RedirectResponse("/admin/smoke-tests?msg=started", status_code=303)


@router.get("/smoke-tests/artifact/{relpath:path}")
def smoke_tests_artifact(relpath: str):
    """Serve a run artifact (screenshot / trace.zip / video) from the smoke
    artifacts dir. Path-traversal guarded — only files inside ARTIFACTS_DIR."""
    base = smoke_report.ARTIFACTS_DIR.resolve()
    target = (base / relpath).resolve()
    if base not in target.parents or not target.is_file():
        return HTMLResponse("Artifact not found.", status_code=404)
    return FileResponse(str(target))


# ── User & Role management (admin-only) ───────────────────────────────────────
# The missing piece for a multi-person trial: create logins, assign roles
# (ADMIN / BOOKKEEPING / SALES / READ_ONLY), reset passwords, activate/deactivate.
# Self-lockout guards: you can't change/deactivate your OWN account, and the last
# active ADMIN can't be demoted or deactivated.

def _active_admin_count(db: Session) -> int:
    from app.models.user import User
    from app.constants import UserRole
    return (
        db.query(User)
        .filter(User.role == UserRole.ADMIN, User.is_active == True)  # noqa: E712
        .count()
    )


@router.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request, ok: str = "", error: str = "",
    db: Session = Depends(get_db), admin=Depends(require_admin),
):
    from app.models.user import User
    from app.constants import UserRole
    users = db.query(User).order_by(User.is_active.desc(), User.username).all()
    return templates.TemplateResponse(
        request, "admin/users.html",
        {"users": users, "roles": list(UserRole), "me": admin, "ok": ok, "error": error},
    )


@router.post("/users", response_class=RedirectResponse)
async def users_create(
    request: Request, db: Session = Depends(get_db), admin=Depends(require_admin),
):
    from app.models.user import User
    from app.constants import UserRole
    from app.auth import hash_password
    from sqlalchemy.exc import IntegrityError
    form = await request.form()
    name = str(form.get("name", "")).strip()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    try:
        role = UserRole(str(form.get("role", UserRole.SALES)))
    except ValueError:
        role = UserRole.SALES
    if not name or not username:
        return RedirectResponse("/admin/users?error=" + quote("Name and username are required."), status_code=303)
    if len(password) < 8:
        return RedirectResponse("/admin/users?error=" + quote("Password must be at least 8 characters."), status_code=303)
    if db.query(User).filter(func.lower(User.username) == username.lower()).first() is not None:
        return RedirectResponse("/admin/users?error=" + quote(f"Username '{username}' is already taken."), status_code=303)
    db.add(User(name=name, username=username, password_hash=hash_password(password), role=role, is_active=True))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/admin/users?error=" + quote("Username is already taken."), status_code=303)
    return RedirectResponse("/admin/users?ok=" + quote(f"Created user '{username}' ({role})."), status_code=303)


@router.post("/users/{user_id}/role", response_class=RedirectResponse)
async def users_set_role(
    user_id: int, request: Request, db: Session = Depends(get_db), admin=Depends(require_admin),
):
    from app.models.user import User
    from app.constants import UserRole
    form = await request.form()
    try:
        new_role = UserRole(str(form.get("role", "")))
    except ValueError:
        return RedirectResponse("/admin/users?error=" + quote("Invalid role."), status_code=303)
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        return RedirectResponse("/admin/users?error=" + quote("User not found."), status_code=303)
    if u.id == admin.id and new_role != UserRole.ADMIN:
        return RedirectResponse("/admin/users?error=" + quote("You can't change your own role."), status_code=303)
    if u.role == UserRole.ADMIN and new_role != UserRole.ADMIN and _active_admin_count(db) <= 1:
        return RedirectResponse("/admin/users?error=" + quote("Can't demote the last active admin."), status_code=303)
    u.role = new_role
    db.commit()
    return RedirectResponse("/admin/users?ok=" + quote(f"{u.username} is now {new_role}."), status_code=303)


@router.post("/users/{user_id}/password", response_class=RedirectResponse)
async def users_reset_password(
    user_id: int, request: Request, db: Session = Depends(get_db), admin=Depends(require_admin),
):
    from app.models.user import User
    from app.auth import hash_password
    form = await request.form()
    new_pw = str(form.get("new_password", ""))
    if len(new_pw) < 8:
        return RedirectResponse("/admin/users?error=" + quote("Password must be at least 8 characters."), status_code=303)
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        return RedirectResponse("/admin/users?error=" + quote("User not found."), status_code=303)
    u.password_hash = hash_password(new_pw)
    db.commit()
    return RedirectResponse("/admin/users?ok=" + quote(f"Reset password for {u.username}."), status_code=303)


@router.post("/users/{user_id}/toggle-active", response_class=RedirectResponse)
async def users_toggle_active(
    user_id: int, db: Session = Depends(get_db), admin=Depends(require_admin),
):
    from app.models.user import User
    from app.constants import UserRole
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        return RedirectResponse("/admin/users?error=" + quote("User not found."), status_code=303)
    if u.id == admin.id:
        return RedirectResponse("/admin/users?error=" + quote("You can't deactivate your own account."), status_code=303)
    if u.is_active and u.role == UserRole.ADMIN and _active_admin_count(db) <= 1:
        return RedirectResponse("/admin/users?error=" + quote("Can't deactivate the last active admin."), status_code=303)
    u.is_active = not u.is_active
    db.commit()
    return RedirectResponse(
        "/admin/users?ok=" + quote(f"{u.username} {'activated' if u.is_active else 'deactivated'}."),
        status_code=303,
    )
