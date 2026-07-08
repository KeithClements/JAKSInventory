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

from fastapi.templating import Jinja2Templates

from app.deps import get_current_user_id, get_db
from app.models.customer import Customer
from app.models.product import ProductCategory, Brand
from app.services.pricing_service import (
    CustomerPriceRuleService,
    CustomerPriceRuleBulkValidationError,
    CustomerPriceRuleValidationError,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["pricing-rules"])
templates = Jinja2Templates(directory="app/templates")


def build_customer_rules_context(db: Session, customer: Customer) -> dict:
    """Build the "Pricing & Deals" panel context for one customer.

    Single source of truth for the per-rule dicts the customer-detail view and
    the HTMX panel-refresh responses both render. Returns exactly the shape the
    pricing_deals_panel macro (via _pricing_deals_panel.html) consumes:

        {
          "rules":           [ {rule_id, customer_id, scope_type, scope_ref,
                                 scope_label, price_method, price_value, qty_min,
                                 effective_from, effective_to, is_active,
                                 margin_pct, below_target, below_cost, note}, ... ],
          "deal_categories": [ {id, label}, ... ],   # Quick Deal Category select
          "deal_brands":     [ {id, label}, ... ],   # Quick Deal Brand select
          "customer_id":     int,
        }

    Only ACTIVE rules are listed (matches the panel's add/edit/deactivate flow —
    a deactivated rule drops out of the panel after the swap). effective_from /
    effective_to are passed through as ORM date attrs so the macro's .strftime()
    works.
    """
    rule_svc = CustomerPriceRuleService(db)
    active_rules = rule_svc.list_rules(customer.id, include_inactive=False)
    rules: list[dict] = []
    for r in active_rules:
        pv = rule_svc.rule_preview(r)  # carries scope_label, margin_pct, below_cost
        rules.append({
            "rule_id": r.id,            # per-row Deactivate button POST target
            "customer_id": customer.id,  # Phase 2: Edit dispatch retargets this customer
            "scope_type": r.scope_type,
            "scope_ref": r.scope_ref,   # Phase 2: Edit re-selects the category/brand option
            "scope_label": pv["scope_label"],
            "price_method": r.price_method,
            "price_value": r.price_value,
            "qty_min": r.qty_min,
            "effective_from": r.effective_from,
            "effective_to": r.effective_to,
            "is_active": r.is_active,
            "margin_pct": pv["margin_pct"],
            "below_target": pv["below_target"],
            "below_cost": pv["below_cost"],
            "note": r.note,
        })

    # Quick Deal modal scope-picker options: {id, label} pairs.
    _deal_categories = sorted(
        db.query(ProductCategory).filter(ProductCategory.is_active == True).all(),  # noqa: E712
        key=lambda cat: cat.full_path.lower(),
    )
    deal_categories = [{"id": cat.id, "label": cat.full_path} for cat in _deal_categories]
    _deal_brands = (
        db.query(Brand).filter(Brand.is_active == True)  # noqa: E712
        .order_by(Brand.sort_order, Brand.name).all()
    )
    deal_brands = [{"id": b.name, "label": b.name} for b in _deal_brands]

    return {
        "rules": rules,
        "deal_categories": deal_categories,
        "deal_brands": deal_brands,
        "customer_id": customer.id,
    }


def _is_htmx(request: Request) -> bool:
    """True when the request came from an HTMX-driven panel mutation. The panel
    handlers send `HX-Request: true`; absence keeps the legacy JSON contract."""
    return str(request.headers.get("HX-Request", "")).lower() == "true"


def _panel_response(request: Request, db: Session, customer: Customer, status_code: int = 200):
    """Render the "Pricing & Deals" panel partial with a freshly-built context so
    HTMX can swap #pricing-deals-panel after a successful mutation."""
    ctx = build_customer_rules_context(db, customer)
    return templates.TemplateResponse(
        request,
        "customers/_pricing_deals_panel.html",
        {
            **ctx,
            "target_label": "Customer-specific cost-plus rules — margins shown at current cost, red under target.",
        },
        status_code=status_code,
    )


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
    """Create a price rule for a customer.

    HTMX (HX-Request header) → re-rendered #pricing-deals-panel partial (the
    client swaps it in place). Otherwise → the legacy JSON {ok, rule} contract."""
    customer = _customer_or_404(db, customer_id)
    if customer is None:
        return JSONResponse({"ok": False, "error": "Customer not found."}, status_code=404)
    data = await _payload(request)
    svc = CustomerPriceRuleService(db, user_id)
    try:
        rule = svc.create_rule(customer_id, data)
    except CustomerPriceRuleValidationError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if _is_htmx(request):
        return _panel_response(request, db, customer, status_code=201)
    return JSONResponse(
        jsonable_encoder({"ok": True, "rule": svc.rule_preview(rule)}), status_code=201
    )


async def _bulk_rows(request: Request) -> list | None:
    """Extract the rule rows from a bulk-create body. Accepts both
    ``{"rules": [...]}`` and a bare top-level list. Returns the list (possibly
    empty) or None when the body is malformed / not a list-or-rules-object."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON
        return None
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        rules = body.get("rules")
        if rules is None:
            return None
        return rules if isinstance(rules, list) else None
    return None


# NOTE: registered BEFORE the /{rule_id} route so the literal "bulk" segment is
# matched here and never captured as a rule_id path param.
@router.post("/{customer_id}/price-rules/bulk")
async def create_price_rules_bulk(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Transactional bulk-create of price rules for a customer (all-or-nothing).

    Body: ``{"rules": [ {scope_type, scope_ref, price_method, price_value,
    qty_min?, effective_from?, effective_to?, note?}, ... ]}`` (a bare top-level
    list is also accepted). EVERY row is validated first; if ANY row is invalid
    the whole batch is rejected (400, error list naming each offending index) and
    NOTHING is written. On success → 201 ``{"ok": True, "created": [<preview>...]}``.
    """
    customer = _customer_or_404(db, customer_id)
    if customer is None:
        return JSONResponse({"ok": False, "error": "Customer not found."}, status_code=404)
    rows = await _bulk_rows(request)
    if rows is None:
        return JSONResponse(
            {"ok": False, "error": "Expected a JSON body {\"rules\": [...]} or a list of rules."},
            status_code=400,
        )
    if len(rows) == 0:
        return JSONResponse(
            {"ok": False, "error": "No rules supplied — 'rules' must be a non-empty list."},
            status_code=400,
        )
    svc = CustomerPriceRuleService(db, user_id)
    try:
        rules = svc.create_rules_bulk(customer_id, rows)
    except CustomerPriceRuleBulkValidationError as exc:
        return JSONResponse({"ok": False, "errors": exc.errors}, status_code=400)
    except CustomerPriceRuleValidationError as exc:
        # e.g. empty-rows guard inside the service — surface as a single message.
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if _is_htmx(request):
        return _panel_response(request, db, customer, status_code=201)
    return JSONResponse(
        jsonable_encoder({"ok": True, "created": [svc.rule_preview(r) for r in rules]}),
        status_code=201,
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
    """Update a price rule (PATCH for JSON clients; POST alias for form/HTMX).

    HTMX (HX-Request header) → re-rendered #pricing-deals-panel partial.
    Otherwise → the legacy JSON {ok, rule} contract."""
    customer = _customer_or_404(db, customer_id)
    if customer is None:
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
    if _is_htmx(request):
        return _panel_response(request, db, customer)
    return JSONResponse(jsonable_encoder({"ok": True, "rule": svc.rule_preview(rule)}))


@router.post("/{customer_id}/price-rules/{rule_id}/deactivate")
def deactivate_price_rule(
    customer_id: int,
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Soft-deactivate a price rule (is_active=False; kept for history/audit).

    HTMX (HX-Request header) → re-rendered #pricing-deals-panel partial (the
    deactivated rule drops out of the active list). Otherwise → legacy JSON."""
    customer = _customer_or_404(db, customer_id)
    if customer is None:
        return JSONResponse({"ok": False, "error": "Customer not found."}, status_code=404)
    svc = CustomerPriceRuleService(db, user_id)
    rule = svc.get_rule(rule_id)
    if rule is None or rule.customer_id != customer_id:
        return JSONResponse({"ok": False, "error": "Price rule not found."}, status_code=404)
    rule = svc.deactivate_rule(rule_id)
    if _is_htmx(request):
        return _panel_response(request, db, customer)
    return JSONResponse(jsonable_encoder({"ok": True, "rule": svc.rule_preview(rule)}))
