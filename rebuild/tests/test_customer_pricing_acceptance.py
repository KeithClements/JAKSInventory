"""
tests/test_customer_pricing_acceptance.py
=========================================
INDEPENDENT acceptance tests for Customer-Specific Product Pricing
(CUSTOMER_PRICING_DESIGN.md), written from the parts-counter's point of view and
exercised end-to-end through the HTTP route + service layer.

This file deliberately does NOT touch tests/test_customer_pricing.py (the
backend unit suite). It is adversarial verification of the *behaviour a clerk
sees*: create a deal through the route, then watch a quote line pick it up;
precedence/qty/date/override/margin-guard/backward-compat/deactivate/validation;
and the last-price hint. Where a property is only observable on the transient
line render-context (pricing_ctx), we call QuoteService.add_line directly against
the same DB the route uses — still the real funnel
(add_line → apply_product_line_defaults → resolve_customer_price), just without
throwing away the in-memory object the HTTP layer renders and discards.

Run:  python -m pytest tests/test_customer_pricing_acceptance.py -q
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.constants import InvoiceStatus, ProductStatus, ScopeType
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.pricing import CustomerPriceRule
from app.models.product import Product, ProductCategory
from app.models.quote import QuoteLine
from app.services.quote_service import QuoteService
from app.main import app
from tests.conftest import activate, fresh_engine

# Own, isolated in-memory engine (conftest pattern) so we never share data with
# the backend unit module or any other lane's test file.
_ENGINE = fresh_engine()
_UID = 1
_SEQ = {"n": 0}


# ── Fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db, *, tier: str = "standard", discount: float = 0.0,
              tax_rate: float = 0.0, exempt: bool = True) -> Customer:
    c = Customer(
        company_name=f"ACC Cust {_uniq()}", contact_name="Counter",
        email=f"acc{_uniq()}@test.local", pricing_tier=tier,
        discount_pct=discount, is_active=True,
        is_tax_exempt=exempt, tax_rate=tax_rate,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _category(db, name: str, parent_id: int | None = None, level: int = 1) -> ProductCategory:
    cat = ProductCategory(name=f"{name}-{_uniq()}", parent_id=parent_id, level=level, is_active=True)
    db.add(cat); db.commit(); db.refresh(cat)
    return cat


def _product(db, *, cost: float = 100.0, brand: str = "", category_id: int | None = None,
             price_override: float | None = None, markup_pct: float | None = None) -> Product:
    p = Product(
        sku=f"ACC-{_uniq():06d}", title="ACC Part", description="ACC Part",
        cost=cost, brand=brand, category_id=category_id,
        price_override=price_override, markup_pct=markup_pct,
        qty_on_hand=100, status=ProductStatus.ACTIVE, is_active=True,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


def _new_quote_via_http(client, customer_id: int) -> int:
    """Create a quote through the real POST /quotes/new route (303 redirect to
    /quotes/{id}) and return the new quote id."""
    r = client.post(
        "/quotes/new",
        data={"customer_id": str(customer_id), "validity_days": "30"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text
    loc = r.headers["location"]
    return int(loc.rstrip("/").split("/")[-1])


def _add_line_service(db, quote_id: int, product_id: int, qty: int = 1) -> QuoteLine:
    """Add a product line through QuoteService.add_line (the real funnel) and
    return the primary line WITH its transient .pricing_ctx attached. Uses the
    same session the route uses, so this is the production path minus the HTML
    render the HTTP handler throws away."""
    svc = QuoteService(db, _UID)
    lines = svc.add_line(quote_id, product_id, {"qty": qty})
    return lines[0]


def _primary_line_price_via_http(client, db, quote_id: int, product_id: int, qty: int = 1) -> float:
    """POST a product line through the real HTTP add-line route, then read the
    persisted unit_price back from the DB (the route renders + discards the
    in-memory object; the saved row is the ground truth a clerk would see)."""
    r = client.post(
        f"/quotes/{quote_id}/lines",
        data={"product_id": str(product_id), "qty": str(qty), "line_type": "product"},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    line = (
        db.query(QuoteLine)
        .filter(QuoteLine.quote_id == quote_id,
                QuoteLine.product_id == product_id,
                QuoteLine.line_type == "product")
        .order_by(QuoteLine.id.desc())
        .first()
    )
    assert line is not None
    return line.unit_price


# ════════════════════════════════════════════════════════════════════════════════
# 1. Create a rule via the route → a quote line picks it up.
# ════════════════════════════════════════════════════════════════════════════════

def test_create_rule_via_route_then_quote_line_applies(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)

    # CREATE a PRODUCT markup rule through the real route.
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 12, "note": "counter deal"},
    )
    assert r.status_code == 201, r.text
    rule_id = r.json()["rule"]["rule_id"]

    # LIST → the preview carries the macro-contract scope_label (the clerk sees a
    # human label, not a raw id).
    r = client.get(f"/customers/{cust.id}/price-rules")
    assert r.status_code == 200
    listed = [rr for rr in r.json()["rules"] if rr["rule_id"] == rule_id]
    assert listed and "scope_label" in listed[0]
    assert prod.sku in listed[0]["scope_label"]

    # ADD that product to a real quote for this customer → the line prices at the
    # cost-plus number (cost 100 × 1.12 = 112.00), straight through the HTTP path.
    qid = _new_quote_via_http(client, cust.id)
    price = _primary_line_price_via_http(client, db, qid, prod.id)
    assert price == 112.00


# ════════════════════════════════════════════════════════════════════════════════
# 2. CATEGORY rule applies to a product tagged to it AND to a child-category product.
# ════════════════════════════════════════════════════════════════════════════════

def test_category_rule_covers_descendant_category(client, db):
    cust = _customer(db)
    top = _category(db, "Engines", level=1)
    child = _category(db, "Heads", parent_id=top.id, level=2)

    prod_top = _product(db, cost=100.0, category_id=top.id)
    prod_child = _product(db, cost=200.0, category_id=child.id)

    # Quick-deal: cost+25% on the WHOLE top category.
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "CATEGORY", "scope_ref": str(top.id),
              "price_method": "markup", "price_value": 25},
    )
    assert r.status_code == 201, r.text

    qid = _new_quote_via_http(client, cust.id)
    # Product directly in the category → 100 × 1.25 = 125.
    assert _primary_line_price_via_http(client, db, qid, prod_top.id) == 125.00
    # Product in the CHILD category → rule still reaches it → 200 × 1.25 = 250.
    assert _primary_line_price_via_http(client, db, qid, prod_child.id) == 250.00


# ════════════════════════════════════════════════════════════════════════════════
# 3. Precedence on a real line: SKU rule beats an "all-turbos" CATEGORY rule, and
#    the overridden runner-up is surfaced ("show both").
# ════════════════════════════════════════════════════════════════════════════════

def test_sku_rule_beats_category_and_shows_overridden(client, db):
    cust = _customer(db)
    turbos = _category(db, "Turbos")
    prod = _product(db, cost=100.0, category_id=turbos.id)

    # All-turbos category deal (the backbone) …
    client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "CATEGORY", "scope_ref": str(turbos.id),
              "price_method": "markup", "price_value": 20},
    )
    # … plus a sharper SKU deal (the scalpel).
    client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 50},
    )

    qid = _new_quote_via_http(client, cust.id)
    line = _add_line_service(db, qid, prod.id)

    # SKU price wins: 100 × 1.50 = 150 (NOT the category's 120).
    assert line.unit_price == 150.00
    cp = line.pricing_ctx["customer_price"]
    assert cp["price"] == 150.00
    assert cp["source_rule"]["scope_type"] == ScopeType.PRODUCT
    # The overridden runner-up (the category rule) is populated for "show both".
    assert cp["overridden_rule"] is not None
    assert cp["overridden_rule"]["scope_type"] == ScopeType.CATEGORY
    assert cp["overridden_rule"]["scope_label"] == turbos.name


# ════════════════════════════════════════════════════════════════════════════════
# 4. Quantity break: qty 5 → non-deal price, qty 10 → deal price.
# ════════════════════════════════════════════════════════════════════════════════

def test_quantity_break_threshold(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)  # selling_price = 130.00

    # Volume deal: cost+5% (=105) only at qty >= 10.
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "CUSTOMER", "price_method": "markup",
              "price_value": 5, "qty_min": 10},
    )
    assert r.status_code == 201, r.text

    qid = _new_quote_via_http(client, cust.id)
    # qty 5 — below the break → falls through to the normal sell price (130.00).
    assert _primary_line_price_via_http(client, db, qid, prod.id, qty=5) == 130.00
    # qty 10 — at the break → the deal (105.00).
    assert _primary_line_price_via_http(client, db, qid, prod.id, qty=10) == 105.00


# ════════════════════════════════════════════════════════════════════════════════
# 5. Date window: an expired rule is ignored; an in-window rule applies.
# ════════════════════════════════════════════════════════════════════════════════

def _seed_rule(db, cust, *, scope_type=ScopeType.CUSTOMER, scope_ref=None,
               method="markup", value=10.0, eff_from=None, eff_to=None,
               qty_min=None, active=True) -> CustomerPriceRule:
    """Persist a CustomerPriceRule directly (model level) for resolution-focused
    tests — verifies the *resolution* behavior end-to-end on a real quote line
    without round-tripping through the create route."""
    r = CustomerPriceRule(
        customer_id=cust.id, scope_type=scope_type,
        scope_ref=(str(scope_ref) if scope_ref is not None else None),
        price_method=method, price_value=value, qty_min=qty_min,
        effective_from=eff_from, effective_to=eff_to, is_active=active,
    )
    db.add(r); db.commit(); db.refresh(r)
    return r


def test_date_window_expired_ignored_active_applies(client, db):
    """The clerk-visible date-window contract on a real quote line: an EXPIRED
    rule is ignored (line falls back to normal price); an IN-WINDOW rule applies.

    Rules are seeded at the model level to exercise resolution directly through
    the line funnel; the create route's date serialization is covered separately
    by test_dated_rule_create_route_serializes."""
    today = date.today()

    # Expired window → ignored → normal sell price (130.00).
    cust_exp = _customer(db)
    prod_exp = _product(db, cost=100.0, markup_pct=30.0)  # 130.00 normal
    _seed_rule(db, cust_exp, value=5.0,
               eff_from=today - timedelta(days=30), eff_to=today - timedelta(days=1))
    qid = _new_quote_via_http(client, cust_exp.id)
    assert _primary_line_price_via_http(client, db, qid, prod_exp.id) == 130.00

    # In-window → applies (cost 100 +15% = 115.00).
    cust_ok = _customer(db)
    prod_ok = _product(db, cost=100.0)
    _seed_rule(db, cust_ok, value=15.0,
               eff_from=today - timedelta(days=1), eff_to=today + timedelta(days=30))
    qid2 = _new_quote_via_http(client, cust_ok.id)
    assert _primary_line_price_via_http(client, db, qid2, prod_ok.id) == 115.00


def test_dated_rule_create_route_serializes(client, db):
    """A rule WITH effective dates creates through the route (201) and comes back
    with ISO date strings. Regression guard for the JSONResponse/datetime.date 500
    QA caught — fixed via fastapi.encoders.jsonable_encoder in pricing_rules.py."""
    cust = _customer(db)
    today = date.today()
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "CUSTOMER", "price_method": "markup", "price_value": 15,
              "effective_from": str(today - timedelta(days=1)),
              "effective_to": str(today + timedelta(days=30))},
    )
    assert r.status_code == 201, r.text
    rule = r.json().get("rule", {})
    assert rule.get("effective_from") == str(today - timedelta(days=1))
    assert rule.get("effective_to") == str(today + timedelta(days=30))


# ════════════════════════════════════════════════════════════════════════════════
# 6. A customer rule OVERRIDES product.price_override.
# ════════════════════════════════════════════════════════════════════════════════

def test_rule_overrides_product_price_override(client, db):
    cust = _customer(db)
    # Product carries a hard sell-price override of 999, but the customer rule wins.
    prod = _product(db, cost=100.0, price_override=999.0)
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 20},
    )
    assert r.status_code == 201, r.text

    qid = _new_quote_via_http(client, cust.id)
    # cost 100 × 1.20 = 120 — NOT the 999 override.
    assert _primary_line_price_via_http(client, db, qid, prod.id) == 120.00


# ════════════════════════════════════════════════════════════════════════════════
# 7. Margin guard is WARN-only: a below-target / below-cost rule still saves the
#    line, and the flags are exposed on the line render-context.
# ════════════════════════════════════════════════════════════════════════════════

def test_margin_guard_below_target_is_warn_only(client, db):
    cust = _customer(db)
    # cost 100 → bracket target is 40%. A 30%-margin rule resolves BELOW target.
    prod = _product(db, cost=100.0)
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "margin", "price_value": 30},
    )
    assert r.status_code == 201, r.text

    qid = _new_quote_via_http(client, cust.id)
    line = _add_line_service(db, qid, prod.id)

    # Line created successfully (never blocked) and priced 100/(1-0.30) = 142.86.
    assert line.id is not None
    cp = line.pricing_ctx["customer_price"]
    assert cp["price"] == 142.86
    assert cp["below_target"] is True   # warn flag exposed
    assert cp["below_cost"] is False


def test_margin_guard_below_cost_is_warn_only(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0)
    # A negative markup resolves BELOW cost (90.00 < 100). The engine must flag it
    # but never block. (price_value < 0 is rejected by the CRUD validator, so this
    # below-cost deal can only be created at the model level — exactly the kind of
    # data the guard exists to catch. We seed the rule row directly, then verify
    # the line still saves and the flag fires.)
    db.add(CustomerPriceRule(
        customer_id=cust.id, scope_type=ScopeType.PRODUCT, scope_ref=str(prod.id),
        price_method="markup", price_value=-10.0, is_active=True,
    ))
    db.commit()

    qid = _new_quote_via_http(client, cust.id)
    line = _add_line_service(db, qid, prod.id)
    assert line.id is not None
    cp = line.pricing_ctx["customer_price"]
    assert cp["price"] == 90.00
    assert cp["below_cost"] is True     # stronger warn flag fires
    assert cp["below_target"] is True   # below cost ⇒ also below target


# ════════════════════════════════════════════════════════════════════════════════
# 8. Backward-compat: a customer with NO rules prices exactly like the normal path.
# ════════════════════════════════════════════════════════════════════════════════

def test_backward_compat_no_rules_matches_normal_path(client, db):
    # CONTROL: a customer with NO price rules.
    cust_ctrl = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)  # selling_price = 130.00
    qid_ctrl = _new_quote_via_http(client, cust_ctrl.id)
    control_price = _primary_line_price_via_http(client, db, qid_ctrl, prod.id)

    # The control must equal the product's normal selling price (no feature effect).
    assert control_price == prod.selling_price == 130.00

    # A SECOND customer, also with no rules, on the same product → identical price.
    cust_feat = _customer(db)
    qid_feat = _new_quote_via_http(client, cust_feat.id)
    feature_price = _primary_line_price_via_http(client, db, qid_feat, prod.id)
    assert feature_price == control_price

    # And no customer_price chip is stashed when there is no rule.
    line = _add_line_service(db, qid_feat, prod.id)
    assert "customer_price" not in (line.pricing_ctx or {})


# ════════════════════════════════════════════════════════════════════════════════
# 9. Deactivate: a deactivated rule no longer prices a new line, and is hidden
#    from the active-only list.
# ════════════════════════════════════════════════════════════════════════════════

def test_deactivate_rule_stops_applying(client, db):
    cust = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)  # 130.00 normal

    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 12},
    )
    assert r.status_code == 201, r.text
    rule_id = r.json()["rule"]["rule_id"]

    # While active → line prices at the deal (112.00).
    qid1 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid1, prod.id) == 112.00

    # DEACTIVATE through the route.
    r = client.post(f"/customers/{cust.id}/price-rules/{rule_id}/deactivate")
    assert r.status_code == 200
    assert r.json()["rule"]["is_active"] is False

    # A NEW line no longer gets the deal → falls back to the normal sell price.
    qid2 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid2, prod.id) == 130.00

    # active_only list excludes it.
    r = client.get(f"/customers/{cust.id}/price-rules?active_only=1")
    assert all(rr["rule_id"] != rule_id for rr in r.json()["rules"])


# ════════════════════════════════════════════════════════════════════════════════
# 10. Validation: bad payloads → 400; unknown customer / rule → 404.
# ════════════════════════════════════════════════════════════════════════════════

def test_validation_bad_payloads_400(client, db):
    cust = _customer(db)

    # bad price_method
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "CUSTOMER", "price_method": "bogus", "price_value": 10},
    )
    assert r.status_code == 400 and r.json()["ok"] is False

    # margin value >= 100
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "CUSTOMER", "price_method": "margin", "price_value": 100},
    )
    assert r.status_code == 400

    # missing scope_ref for a non-CUSTOMER scope
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "BRAND", "price_method": "markup", "price_value": 10},
    )
    assert r.status_code == 400


def test_unknown_customer_and_rule_404(client, db):
    # Unknown customer on create.
    r = client.post(
        "/customers/99999999/price-rules",
        json={"scope_type": "CUSTOMER", "price_method": "markup", "price_value": 10},
    )
    assert r.status_code == 404

    # Unknown customer on list.
    r = client.get("/customers/99999999/price-rules")
    assert r.status_code == 404

    # Unknown rule under a real customer (deactivate + update).
    cust = _customer(db)
    r = client.post(f"/customers/{cust.id}/price-rules/99999999/deactivate")
    assert r.status_code == 404
    r = client.patch(
        f"/customers/{cust.id}/price-rules/99999999",
        json={"scope_type": "CUSTOMER", "price_method": "markup", "price_value": 10},
    )
    assert r.status_code == 404


# ════════════════════════════════════════════════════════════════════════════════
# 11. Last-price hint: prior FINALIZED invoice surfaces on a new quote line when no
#     rule matches; draft/void invoices are ignored.
# ════════════════════════════════════════════════════════════════════════════════

def _invoice_line(db, cust, prod, unit_price, status):
    inv = Invoice(
        invoice_number=f"INV-ACC-{_uniq():06d}", customer_id=cust.id, status=status,
    )
    db.add(inv); db.commit(); db.refresh(inv)
    line = InvoiceLine(
        invoice_id=inv.id, product_id=prod.id, qty=1,
        unit_price=unit_price, unit_cost=50.0,
    )
    db.add(line); db.commit(); db.refresh(line)
    return inv


def test_last_price_hint_from_finalized_invoice(client, db):
    cust = _customer(db)
    prod = _product(db, cost=50.0, markup_pct=30.0)  # no rule → normal pricing

    # A prior FINALIZED (OPEN) invoice for this customer+product at 137.50.
    inv = _invoice_line(db, cust, prod, 137.50, InvoiceStatus.OPEN)

    # New quote line, no rule → unit_price falls through to normal pricing, but the
    # last-price hint carries the prior finalized number for click-to-apply.
    qid = _new_quote_via_http(client, cust.id)
    line = _add_line_service(db, qid, prod.id)

    assert "customer_price" not in (line.pricing_ctx or {})  # no rule
    hint = line.pricing_ctx.get("last_price")
    assert hint is not None
    assert hint["unit_price"] == 137.50
    assert hint["doc_ref"] == inv.invoice_number


def test_last_price_hint_ignores_draft_and_void(client, db):
    cust = _customer(db)
    prod = _product(db, cost=50.0)

    # Only DRAFT + VOID history → no usable hint.
    _invoice_line(db, cust, prod, 200.0, InvoiceStatus.DRAFT)
    _invoice_line(db, cust, prod, 300.0, InvoiceStatus.VOID)

    qid = _new_quote_via_http(client, cust.id)
    line = _add_line_service(db, qid, prod.id)
    assert line.pricing_ctx.get("last_price") is None


# ════════════════════════════════════════════════════════════════════════════════
# PHASE 2 — bulk-create endpoint + edit (PATCH) flow, clerk-visible / end-to-end.
#
# The backend unit suite (tests/test_customer_pricing.py) already asserts the bulk
# service contract at the row level. Here we complement at the parts-counter level:
# a clerk POSTs a batch of deals, then watches each one actually price a REAL quote
# line for the right product; and a clerk EDITS a live deal and watches the new
# number take over on the next line. Everything below rides the HTTP route + the
# real add_line funnel — no service shortcuts for the assertions that matter.
# ════════════════════════════════════════════════════════════════════════════════


def _rules_via_route(client, customer_id: int, *, active_only: bool = False) -> list[dict]:
    """The customer's price-rule previews as a clerk's deal list would show them
    (real GET route). Used to prove all-or-nothing at the visible surface."""
    suffix = "?active_only=1" if active_only else ""
    r = client.get(f"/customers/{customer_id}/price-rules{suffix}")
    assert r.status_code == 200, r.text
    return r.json()["rules"]


# ── Task 1: bulk happy path — 3 mixed rules → 201 → each applies on a real line ──

def test_bulk_create_mixed_rules_each_applies_on_a_quote_line(client, db):
    """A clerk posts ONE batch of three different deals (a category margin deal, a
    brand markup deal, and a whole-account markup deal) → 201; then each deal
    actually prices the matching product on a live quote line at the right number.

    Products are picked so precedence is unambiguous (each lands on exactly one
    rule): prod_cat is in the category but carries no brand; prod_brand carries the
    brand but no category; prod_other matches neither (only the CUSTOMER rule)."""
    cust = _customer(db)
    cat = _category(db, "BulkCat")

    # cost 100, category-tagged, no brand → only the CATEGORY rule reaches it.
    prod_cat = _product(db, cost=100.0, category_id=cat.id, markup_pct=30.0)  # 130 normal
    # cost 200, branded, no category → only the BRAND rule reaches it.
    prod_brand = _product(db, cost=200.0, brand="BulkBrand", markup_pct=30.0)  # 260 normal
    # cost 50, no brand/category → only the whole-account CUSTOMER rule reaches it.
    prod_other = _product(db, cost=50.0, markup_pct=30.0)  # 65 normal

    r = client.post(
        f"/customers/{cust.id}/price-rules/bulk",
        json={"rules": [
            {"scope_type": "CATEGORY", "scope_ref": str(cat.id),
             "price_method": "margin", "price_value": 40, "note": "cat deal"},
            {"scope_type": "BRAND", "scope_ref": "BulkBrand",
             "price_method": "markup", "price_value": 15},
            {"scope_type": "CUSTOMER",
             "price_method": "markup", "price_value": 10},
        ]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True and len(body["created"]) == 3

    qid = _new_quote_via_http(client, cust.id)
    # CATEGORY margin 40 on cost 100 → 100 / (1 - 0.40) = 166.67
    assert _primary_line_price_via_http(client, db, qid, prod_cat.id) == 166.67
    # BRAND markup 15 on cost 200 → 230.00
    assert _primary_line_price_via_http(client, db, qid, prod_brand.id) == 230.00
    # CUSTOMER markup 10 on cost 50 → 55.00 (the whole-account deal catches it)
    assert _primary_line_price_via_http(client, db, qid, prod_other.id) == 55.00


# ── Task 2: bulk all-or-nothing via the route — bad row 1 → 400, nothing written ─

def test_bulk_all_or_nothing_via_route_writes_nothing(client, db):
    """A 3-row batch where index 1 is invalid (margin >= 100) → 400, the error list
    names index 1, and a follow-up GET of the customer's rules shows ZERO of the
    batch persisted (not even the two valid rows). The clerk's deal list is the
    ground truth for 'nothing partially written'."""
    cust = _customer(db)
    prod = _product(db, cost=100.0)

    before = _rules_via_route(client, cust.id)
    assert before == []  # fresh customer, no deals yet

    r = client.post(
        f"/customers/{cust.id}/price-rules/bulk",
        json={"rules": [
            {"scope_type": "PRODUCT", "scope_ref": str(prod.id),
             "price_method": "markup", "price_value": 10},      # 0 — valid
            {"scope_type": "CUSTOMER", "price_method": "margin",
             "price_value": 100},                                # 1 — margin >= 100
            {"scope_type": "CUSTOMER", "price_method": "markup",
             "price_value": 5},                                  # 2 — valid
        ]},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["ok"] is False
    bad_indexes = {e["index"] for e in body["errors"]}
    assert 1 in bad_indexes              # the offending row is named
    assert 0 not in bad_indexes          # the valid rows are NOT reported
    assert 2 not in bad_indexes

    # ALL-OR-NOTHING at the visible surface: the deal list is still empty.
    after = _rules_via_route(client, cust.id)
    assert after == []


def test_bulk_all_or_nothing_missing_scope_ref(client, db):
    """A second all-or-nothing flavor: index 1 is invalid because a non-CUSTOMER
    rule omits scope_ref. 400, index 1 named, and the prior valid row 0 is NOT
    persisted (the clerk sees no new deals)."""
    cust = _customer(db)
    prod = _product(db, cost=100.0)

    r = client.post(
        f"/customers/{cust.id}/price-rules/bulk",
        json={"rules": [
            {"scope_type": "PRODUCT", "scope_ref": str(prod.id),
             "price_method": "markup", "price_value": 10},      # 0 — valid
            {"scope_type": "CATEGORY",                           # 1 — no scope_ref
             "price_method": "markup", "price_value": 20},
        ]},
    )
    assert r.status_code == 400, r.text
    assert 1 in {e["index"] for e in r.json()["errors"]}
    assert _rules_via_route(client, cust.id) == []


# ── Task 3: bulk bare top-level list body is accepted ───────────────────────────

def test_bulk_bare_list_body_accepted_and_applies(client, db):
    """The route accepts a bare top-level JSON list (no {"rules": ...} wrapper) →
    201, the deals persist, and one of them prices a real quote line."""
    cust = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)  # 130 normal

    r = client.post(
        f"/customers/{cust.id}/price-rules/bulk",
        json=[
            {"scope_type": "PRODUCT", "scope_ref": str(prod.id),
             "price_method": "markup", "price_value": 12},
            {"scope_type": "CUSTOMER", "price_method": "margin", "price_value": 30},
        ],
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["created"]) == 2
    assert len(_rules_via_route(client, cust.id)) == 2

    # The PRODUCT deal (markup 12 on cost 100) wins on a real line → 112.00.
    qid = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid, prod.id) == 112.00


# ── Task 4: edit changes the resolved price on the NEXT line ─────────────────────

def test_edit_rule_changes_resolved_price_on_new_line(client, db):
    """A clerk creates a +10% product deal (line prices at 110.00), then PATCHes the
    same rule to +25%; a NEW quote line for that product now prices at 125.00. The
    edit is observed end-to-end on real lines, not just the echoed preview."""
    cust = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)  # 130 normal

    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 10},
    )
    assert r.status_code == 201, r.text
    rule_id = r.json()["rule"]["rule_id"]

    # Before the edit: cost 100 × 1.10 = 110.00.
    qid1 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid1, prod.id) == 110.00

    # PATCH the deal to +25%. (update_rule re-validates the FULL payload, so we
    # resend scope_type/scope_ref/price_method alongside the new value.)
    r = client.patch(
        f"/customers/{cust.id}/price-rules/{rule_id}",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 25},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["rule"]["price_value"] == 25.0

    # After the edit: a NEW line for the same product prices at 100 × 1.25 = 125.00.
    qid2 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid2, prod.id) == 125.00


def test_edit_rule_can_switch_method_markup_to_margin(client, db):
    """An edit can also change the METHOD: a markup deal flipped to a margin deal
    re-prices the next line by the margin formula."""
    cust = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)

    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 10},  # → 110.00
    )
    rule_id = r.json()["rule"]["rule_id"]
    qid1 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid1, prod.id) == 110.00

    # Flip to margin 40 → 100 / (1 - 0.40) = 166.67.
    r = client.patch(
        f"/customers/{cust.id}/price-rules/{rule_id}",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "margin", "price_value": 40},
    )
    assert r.status_code == 200, r.text
    qid2 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid2, prod.id) == 166.67


# ── Task 5: edit validation — bad value → 400 and the rule is unchanged ──────────

def test_edit_rule_invalid_value_400_and_rule_unchanged(client, db):
    """A PATCH with an invalid value (margin >= 100, then a negative value) → 400,
    and a re-fetch of the rule shows its value UNCHANGED — and, decisively, a new
    quote line still prices at the ORIGINAL deal (the bad edit never landed)."""
    cust = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)  # 130 normal

    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 20},  # → 120.00
    )
    rule_id = r.json()["rule"]["rule_id"]

    # Invalid edit #1: margin >= 100.
    r = client.patch(
        f"/customers/{cust.id}/price-rules/{rule_id}",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "margin", "price_value": 100},
    )
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False

    # Invalid edit #2: negative value.
    r = client.patch(
        f"/customers/{cust.id}/price-rules/{rule_id}",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": -5},
    )
    assert r.status_code == 400, r.text

    # Re-fetch: the stored rule still says markup 20 (neither bad edit landed).
    listed = _rules_via_route(client, cust.id)
    hit = next(rr for rr in listed if rr["rule_id"] == rule_id)
    assert hit["price_method"] == "markup"
    assert hit["price_value"] == 20.0

    # And the live behavior is unchanged: a new line still prices at 120.00.
    qid = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid, prod.id) == 120.00


# ── Task 6: edit scope/qty/date round-trip — raised qty_min re-gates a line ──────

def test_edit_raises_qty_min_and_regate_takes_effect(client, db):
    """A deal that qualifies at qty 5 is edited to require qty_min 10; a fresh qty-5
    line that previously got the deal now falls back to the normal sell price, while
    a qty-10 line gets the (re-gated) deal. The edited gating takes effect on real
    lines, both directions."""
    cust = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)  # 130 normal

    # Whole-account deal, cost+5% (=105), NO qty gate yet → qty 5 qualifies.
    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "CUSTOMER", "price_method": "markup", "price_value": 5},
    )
    assert r.status_code == 201, r.text
    rule_id = r.json()["rule"]["rule_id"]

    qid1 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid1, prod.id, qty=5) == 105.00

    # PATCH: raise the gate to qty_min 10.
    r = client.patch(
        f"/customers/{cust.id}/price-rules/{rule_id}",
        json={"scope_type": "CUSTOMER", "price_method": "markup",
              "price_value": 5, "qty_min": 10},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rule"]["qty_min"] == 10.0

    # After the edit: qty 5 NO LONGER qualifies → normal sell price (130.00) …
    qid2 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid2, prod.id, qty=5) == 130.00
    # … but qty 10 DOES → the deal (105.00).
    assert _primary_line_price_via_http(client, db, qid2, prod.id, qty=10) == 105.00


def test_edit_effective_dates_expire_a_deal(client, db):
    """The date half of the scope/qty/date round-trip: an always-on deal is edited
    to an EXPIRED window; the next line stops getting it (falls back to normal)."""
    cust = _customer(db)
    prod = _product(db, cost=100.0, markup_pct=30.0)  # 130 normal
    today = date.today()

    r = client.post(
        f"/customers/{cust.id}/price-rules",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 12},  # → 112.00
    )
    rule_id = r.json()["rule"]["rule_id"]
    qid1 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid1, prod.id) == 112.00

    # PATCH the deal into an EXPIRED window.
    r = client.patch(
        f"/customers/{cust.id}/price-rules/{rule_id}",
        json={"scope_type": "PRODUCT", "scope_ref": str(prod.id),
              "price_method": "markup", "price_value": 12,
              "effective_from": str(today - timedelta(days=30)),
              "effective_to": str(today - timedelta(days=1))},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rule"]["effective_to"] == str(today - timedelta(days=1))

    # The expired deal no longer applies → normal sell price (130.00).
    qid2 = _new_quote_via_http(client, cust.id)
    assert _primary_line_price_via_http(client, db, qid2, prod.id) == 130.00
