"""
app/routers/categories.py
=========================
§18 Inventory → Category Maintenance. Owns the master classification structure:
the Category/Subcategory/Product-Family tree plus the Brand and Manufacturer
(Engine-Make) maintained lists. This is the ONLY place that structure is edited —
the Products List just filters and bulk-assigns against it.

All mutations are POST → redirect back to /categories/ with ?ok= / ?error= so a
flash banner renders. Distinct from products.py (no shared file → no lane
collision).
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user_id
from app.services.category_service import CategoryService, LEVEL_LABELS

router = APIRouter(prefix="/categories", tags=["categories"])
templates = Jinja2Templates(directory="app/templates")


def _svc(db: Session, user_id: int | None = None) -> CategoryService:
    return CategoryService(db, current_user_id=user_id)


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _opt_float(v) -> float | None:
    s = str(v or "").strip().replace("%", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _back(ok: str = "", error: str = "") -> RedirectResponse:
    qs = f"?ok={quote(ok)}" if ok else (f"?error={quote(error)}" if error else "")
    return RedirectResponse(f"/categories/{qs}", status_code=303)


# ══ Screen ═════════════════════════════════════════════════════════════════════
@router.get("/", response_class=HTMLResponse)
def category_maintenance(
    request: Request, ok: str = "", error: str = "", db: Session = Depends(get_db)
):
    svc = _svc(db)
    return templates.TemplateResponse(request, "categories/index.html", {
        "tree": svc.category_tree(),
        "categories_flat": svc.categories_flat(),
        "brands": svc.brands(),
        "manufacturers": svc.manufacturers(),
        "level_labels": LEVEL_LABELS,
        "ok": ok,
        "error": error,
    })


# ══ Category CRUD ══════════════════════════════════════════════════════════════
@router.post("/category/create")
async def category_create(request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    try:
        _svc(db, user_id).create_category(
            name=str(form.get("name", "")),
            parent_id=_int(form.get("parent_id")),
            sort_order=_int(form.get("sort_order")) or 0,
            default_markup_pct=_opt_float(form.get("default_markup_pct")),
            import_keywords=str(form.get("import_keywords", "")),
        )
    except ValueError as exc:
        return _back(error=str(exc))
    return _back(ok="Category added.")


@router.post("/category/{cat_id}/update")
async def category_update(cat_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    try:
        _svc(db, user_id).update_category(
            cat_id,
            name=str(form.get("name", "")),
            sort_order=_int(form.get("sort_order")),
            default_markup_pct=_opt_float(form.get("default_markup_pct")),
            import_keywords=str(form.get("import_keywords", "")),
        )
    except ValueError as exc:
        return _back(error=str(exc))
    return _back(ok="Category updated.")


@router.post("/category/{cat_id}/toggle")
async def category_toggle(cat_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    active = str(form.get("active", "")).strip() in ("1", "true", "True", "on")
    try:
        _svc(db, user_id).set_category_active(cat_id, active)
    except ValueError as exc:
        return _back(error=str(exc))
    return _back(ok="Category " + ("activated." if active else "deactivated."))


@router.post("/category/{cat_id}/delete")
async def category_delete(cat_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    action = _svc(db, user_id).delete_category(cat_id)
    return _back(ok=f"Category {action}.")


# ══ Brand CRUD ═════════════════════════════════════════════════════════════════
@router.post("/brand/create")
async def brand_create(request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    try:
        _svc(db, user_id).create_brand(
            name=str(form.get("name", "")),
            is_house_brand=str(form.get("is_house_brand", "")).strip() in ("1", "on", "true"),
            sort_order=_int(form.get("sort_order")) or 0,
        )
    except ValueError as exc:
        return _back(error=str(exc))
    return _back(ok="Brand added.")


@router.post("/brand/{brand_id}/update")
async def brand_update(brand_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    try:
        _svc(db, user_id).update_brand(
            brand_id,
            name=str(form.get("name", "")),
            sort_order=_int(form.get("sort_order")),
            is_house_brand=str(form.get("is_house_brand", "")).strip() in ("1", "on", "true"),
            is_active=str(form.get("is_active", "")).strip() in ("1", "on", "true"),
        )
    except ValueError as exc:
        return _back(error=str(exc))
    return _back(ok="Brand updated.")


@router.post("/brand/{brand_id}/delete")
async def brand_delete(brand_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    _svc(db, user_id).delete_brand(brand_id)
    return _back(ok="Brand removed.")


# ══ Manufacturer CRUD (= Engine Make) ══════════════════════════════════════════
@router.post("/manufacturer/create")
async def manufacturer_create(request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    try:
        _svc(db, user_id).create_manufacturer(
            name=str(form.get("name", "")),
            sort_order=_int(form.get("sort_order")) or 0,
        )
    except ValueError as exc:
        return _back(error=str(exc))
    return _back(ok="Manufacturer added.")


@router.post("/manufacturer/{man_id}/update")
async def manufacturer_update(man_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    try:
        _svc(db, user_id).update_manufacturer(
            man_id,
            name=str(form.get("name", "")),
            sort_order=_int(form.get("sort_order")),
            is_active=str(form.get("is_active", "")).strip() in ("1", "on", "true"),
        )
    except ValueError as exc:
        return _back(error=str(exc))
    return _back(ok="Manufacturer updated.")


@router.post("/manufacturer/{man_id}/delete")
async def manufacturer_delete(man_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    _svc(db, user_id).delete_manufacturer(man_id)
    return _back(ok="Manufacturer removed.")
