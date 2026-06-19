"""
app/routers/pricing_rules.py
============================
Customer-Specific Product Pricing — rule CRUD (CUSTOMER_PRICING_DESIGN.md §5,
Phase 1). View functions only; business logic + validation live in
CustomerPriceRuleService (app/services/pricing_service.py).

Routes (all scoped to one customer):

  GET    /customers/{customer_id}/price-rules          → list (JSON)
  POST   /customers/{customer_id}/price-rules          → create (JSON)
  PATCH  /customers/{customer_id}/price-rules/{rule_id} → update (JSON)
  POST   /customers/{customer_id}/price-rules/{rule_id} → update (form/HTMX alias)
  POST   /customers/{customer_id}/price-rules/{rule_id}/deactivate → soft-delete

Request body fields (create/update; form-encoded OR JSON):
  scope_type      PRODUCT | CATEGORY | BRAND | CUSTOMER   (required)
  scope_ref       product id / category id / brand string (required unless CUSTOMER)
  price_method    markup | margin                          (required)
  price_value     float >= 0  (margin must be < 100)       (required)
  qty_min         float >= 0 | blank                        (optional volume break)
  effective_from  YYYY-MM-DD | blank                        (optional)
  effective_to    YYYY-MM-DD | blank                        (optional)
  note            free text                                 (optional)

Response shape (JSON): {"ok": bool, "rule": {...preview...}} on write, or
{"ok": True, "rules": [{...preview...}, ...]} on list. Each preview carries
margin_at_current_cost (PRODUCT rules) or cost_relative=True (line rules).
Validation failures → HTTP 400 {"ok": False, "error": "..."}.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.services.pricing_service import (
    CustomerPriceRuleService,
    CustomerPriceRuleValidationError,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["pricing-rules"])


async def _payload(request: Request) -> dict:
    """Read a create/update payload from either JSON or form-encoded body."""
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            data = await request.json()
            return dict(data) if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 — fall through to form parsing
            return {}
    form = await request.form()
    return {k: v for k, v in form.items()}


def _customer_or_404(db: Session, customer_id: int) -> Customer | None:
    return db.query(Customer).filter(Customer.id == customer_id).first()


@router.get("/{customer_id}/price-rules")
def list_price_rules(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """List a customer's price rules with per-rule margin-at-current-cost preview.
    ?active_only=1 hides soft-deactivated rules."""
    if _customer_or_404(db, customer_id) is None:
        return JSONResponse({"ok": False, "error": "Customer not found."}, status_code=404)
    active_only = str(request.query_params.get("active_only", "")).lower() in ("1", "true", "yes")
    svc = CustomerPriceRuleService(db, user_id)
    rules = svc.list_rules(customer_id, include_inactive=not active_only)
    # jsonable_encoder coerces datetime.date in the preview (effective_from/_to)
    # to ISO strings — stdlib json.dumps inside JSONResponse can't.
    return JSONResponse(jsonable_encoder({"ok": True, "rules": [svc.rule_preview(r) for r in rules]}))


@router.post("/{customer_id}/price-rules")
async def create_price_rule(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Create a price rule for a customer."""
    if _customer_or_404(db, customer_id) is None:
        return JSONResponse({"ok": False, "error": "Customer not found."}, status_code=404)
    data = await _payload(request)
    svc = CustomerPriceRuleService(db, user_id)
    try:
        rule = svc.create_rule(customer_id, data)
    except CustomerPriceRuleValidationError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        jsonable_encoder({"ok": True, "rule": svc.rule_preview(rule)}), status_code=201
    )


@router.patch("/{customer_id}/price-rules/{rule_id}")
@router.post("/{customer_id}/price-rules/{rule_id}")
async def update_price_rule(
    customer_id: int,
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Update a price rule (PATCH for JSON clients; POST alias for form/HTMX)."""
    if _customer_or_404(db, customer_id) is None:
        return JSONResponse({"ok": False, "error": "Customer not found."}, status_code=404)
    svc = CustomerPriceRuleService(db, user_id)
    rule = svc.get_rule(rule_id)
    if rule is None or rule.customer_id != customer_id:
        return JSONResponse({"ok": False, "error": "Price rule not found."}, status_code=404)
    data = await _payload(request)
    try:
        rule = svc.update_rule(rule_id, data)
    except CustomerPriceRuleValidationError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(jsonable_encoder({"ok": True, "rule": svc.rule_preview(rule)}))


@router.post("/{customer_id}/price-rules/{rule_id}/deactivate")
def deactivate_price_rule(
    customer_id: int,
    rule_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Soft-deactivate a price rule (is_active=False; kept for history/audit)."""
    if _customer_or_404(db, customer_id) is None:
        return JSONResponse({"ok": False, "error": "Customer not found."}, status_code=404)
    svc = CustomerPriceRuleService(db, user_id)
    rule = svc.get_rule(rule_id)
    if rule is None or rule.customer_id != customer_id:
        return JSONResponse({"ok": False, "error": "Price rule not found."}, status_code=404)
    rule = svc.deactivate_rule(rule_id)
    return JSONResponse(jsonable_encoder({"ok": True, "rule": svc.rule_preview(rule)}))
