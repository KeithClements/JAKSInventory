"""
tests/test_template_renders.py
===============================
Jinja-on-ORM regression guards for template rendering correctness.
These tests render templates directly against ORM model instances —
no HTTP server, no login required — so they're fast and stable.

B-1  invoice print.html shows a Tax row when tax_amount > 0
     Guard: prevents a future template edit from removing the {% if tax_amount > 0 %}
     branch and silently omitting tax from printed invoices.

W-4  _match_panel.html does NOT render the "+N over" qty-variance narrative when
     the bill has a cost-variance-only discrepancy (no qty overbill).
     Guard: prevents the qty_var > 0 branch from firing when qty_billed == qty_received
     but cost differs, which would confuse AP staff with a false qty narrative.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_template_renders.py -v
"""
from __future__ import annotations

import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

import app.database as _appdb
from app.main import app
from tests.conftest import activate, fresh_engine

# ---------------------------------------------------------------------------
# Module engine — both test groups share one isolated in-memory DB.
# ---------------------------------------------------------------------------

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Template environment (direct Jinja2, no FastAPI / no request object needed).
# print.html is standalone (DOCTYPE, no extends), so no request is required.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def jenv():
    """Bare Jinja2 env pointed at app/templates — no request needed."""
    return Environment(
        loader=FileSystemLoader("app/templates"),
        autoescape=False,  # print.html uses explicit |e filters; no autoescaping
    )


# ===========================================================================
# B-1  Invoice print.html — Tax row appears when tax_amount > 0
# ===========================================================================

def _make_taxable_invoice(db):
    """Create + finalise a taxable invoice so tax_amount is a real non-zero value."""
    from app.constants import InvoiceStatus, LineType
    from app.models.customer import Customer
    from app.models.invoice import Invoice, InvoiceLine
    from app.services.invoice_service import InvoiceService

    c = Customer(company_name="Tax Print Co", contact_name="QA",
                 email="taxprint@test.local")
    db.add(c); db.flush()

    inv = Invoice(
        invoice_number="INV-TAX-PRINT-001",
        customer_id=c.id,
        status=InvoiceStatus.DRAFT,
        is_taxable=True,
        tax_rate=8.5,
        tax_rate_snapshot=8.5,
        tax_exempt_snapshot=False,
    )
    db.add(inv); db.flush()

    line = InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        description="Taxable part",
        qty=1,
        unit_price=100.0,
        unit_cost=60.0,
        is_taxable=True,
    )
    db.add(line); db.commit()

    # Finalise stamps per-line tax_amount and sets status=OPEN
    InvoiceService(db, 1).finalise(inv.id)
    db.expire_all()

    from app.models.invoice import Invoice as Inv
    return db.query(Inv).filter(Inv.id == inv.id).first()


def test_b1_tax_row_present_when_tax_amount_nonzero(db, jenv):
    """
    invoices/print.html must include a Tax row when invoice.tax_amount > 0.

    The template has:
        {% if invoice.tax_amount > 0 %}
        <div class="t-row">
            <span class="lbl">Tax ({{ invoice.tax_rate_snapshot or invoice.tax_rate }}%)</span>
        {% endif %}

    Guard: this test fails if the branch is removed or the condition is inverted,
    which would silently drop tax from customer-facing printed invoices.
    """
    inv = _make_taxable_invoice(db)

    assert inv.tax_amount > 0, (
        f"Precondition: invoice.tax_amount must be > 0; got {inv.tax_amount}"
    )

    html = jenv.get_template("invoices/print.html").render(
        invoice=inv,
        customer_addr_lines=[inv.customer.address_line1 or "123 Main St"],
        discount_amount=inv.discount_amount,
        company={
            "name": "JAKS Parts", "address": "", "phone": "", "email": ""
        },
    )

    assert "Tax" in html, (
        "print.html must render a 'Tax' row when invoice.tax_amount > 0; "
        "the {% if invoice.tax_amount > 0 %} branch is missing or broken. "
        f"(tax_amount={inv.tax_amount!r}, tax_rate={inv.tax_rate_snapshot!r})"
    )
    # Also verify the tax AMOUNT appears somewhere in the total block
    assert f"{inv.tax_amount:.2f}" in html, (
        f"Expected formatted tax amount '{inv.tax_amount:.2f}' in rendered HTML"
    )


def test_b1_tax_row_absent_when_tax_amount_zero(db, jenv):
    """Counter-case: when invoice is tax-exempt, no Tax row must appear."""
    from app.constants import InvoiceStatus, LineType
    from app.models.customer import Customer
    from app.models.invoice import Invoice, InvoiceLine
    from app.services.invoice_service import InvoiceService

    c = Customer(company_name="Exempt Print Co", contact_name="QA",
                 email="exempt@test.local")
    db.add(c); db.flush()

    inv = Invoice(
        invoice_number="INV-TAX-PRINT-002",
        customer_id=c.id,
        status=InvoiceStatus.DRAFT,
        is_taxable=False,
        tax_rate=0.0,
        tax_rate_snapshot=0.0,
        tax_exempt_snapshot=True,
    )
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT,
                       description="Exempt part", qty=1,
                       unit_price=100.0, unit_cost=60.0, is_taxable=False))
    db.commit()

    InvoiceService(db, 1).finalise(inv.id)
    db.expire_all()

    from app.models.invoice import Invoice as Inv
    inv_r = db.query(Inv).filter(Inv.id == inv.id).first()
    assert inv_r.tax_amount == 0, f"Precondition: tax-exempt invoice tax_amount must be 0; got {inv_r.tax_amount}"

    html = jenv.get_template("invoices/print.html").render(
        invoice=inv_r,
        customer_addr_lines=[],
        discount_amount=0.0,
        company={"name": "JAKS Parts", "address": "", "phone": "", "email": ""},
    )

    # The Tax row class is "t-row" with label "Tax (" — check specifically for the row
    assert "Tax (" not in html, (
        "Tax row must NOT appear when invoice.tax_amount == 0 (tax-exempt)"
    )


# ===========================================================================
# W-4  _match_panel.html — cost-variance-only bill has no "+N" qty narrative
# ===========================================================================

def _make_match_context(*, state: str, qty_var: int, cost_var: float):
    """
    Build a minimal match context matching the shape compute_match_summary returns.
    Only one row in the match table; is_flag=True to exercise the variance display.
    """
    row = {
        "line":           None,     # not accessed by the template
        "ordered_qty":    10,
        "ordered_cost":   12.0,
        "received_qty":   10,
        "billed_qty":     10,
        "billed_cost":    12.0 + cost_var,
        "qty_var":        qty_var,
        "cost_var":       cost_var,
        "state":          state,
        "is_flag":        state in ("over_billed", "cost_variance"),
        "suggested_credit": round(cost_var * 10, 2) if state == "cost_variance" else 0.0,
        "can_resolve":    True,
        "resolution_vcm_id": None,
    }
    return {
        "rows":          [row],
        "flag_count":    1 if row["is_flag"] else 0,
        "matched_count": 0,
        "has_activity":  True,
    }


def test_w4_cost_variance_only_no_qty_over_narrative(jenv):
    """
    _match_panel.html must NOT render the '+N' qty-variance narrative for a
    cost-variance-only bill (where qty_billed == qty_received, qty_var == 0).

    The template at line 87-88 has:
        {% if r.qty_var > 0 %}
        <span ...>+{{ r.qty_var }}</span>

    For a cost-variance row, qty_var == 0 → the branch must not fire.
    Guard: prevents a template change that makes the +N span unconditional.
    """
    # Exact state returned by compute_match_line for a cost-variance-only bill.
    # qty_billed == qty_received → qty_var = 0; cost differs → cost_variance state.
    match = _make_match_context(state="cost_variance", qty_var=0, cost_var=3.00)

    html = jenv.get_template("purchase_orders/_match_panel.html").render(match=match)

    # Must NOT contain the over-qty narrative in any form
    assert "+0" not in html, "qty_var=0 should never produce '+0' in the HTML"
    # More broadly: for a cost-variance row, the badge says 'cost variance' not 'over-billed'
    assert "Cost variance" in html, (
        "cost_variance state must render the 'Cost variance' badge, not over-billed"
    )
    assert "Over-billed" not in html, (
        "cost_variance-only match must NOT show 'Over-billed' badge"
    )


def test_w4_over_billed_does_show_qty_narrative(jenv):
    """
    Counter-case: when state IS over_billed and qty_var > 0, the '+N' span
    MUST appear so AP sees the exact overage quantity.
    """
    match = _make_match_context(state="over_billed", qty_var=2, cost_var=0.0)
    html = jenv.get_template("purchase_orders/_match_panel.html").render(match=match)

    assert "+2" in html, (
        "over_billed state with qty_var=2 must render '+2' in the HTML; "
        "the {% if r.qty_var > 0 %} branch is missing or broken."
    )
    assert "Over-billed" in html, "over_billed state must show 'Over-billed' badge"
