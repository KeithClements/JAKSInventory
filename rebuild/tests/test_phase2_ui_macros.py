"""
tests/test_phase2_ui_macros.py
==============================
Render guards for the Phase-2 shared UI primitives authored by the UI-Architect
lane (JAKS_UI_Change_Plan.md §8M):

  - macros/customer_flags.html   → customer_flags / customer_flag      (Primitive 8, P2-D2)
  - macros/intelligence.html     → intelligence_panel + typed wrappers (Primitive 9, P2-Q1/§5.8)
  - base.html margin toggle      → showMargin / toggleMargin()         (P2-D3)

These are PURE-PRESENTATION macros that must render today against data Backend
has NOT built yet (the flag schema + CustomerMetricsService). The key contracts
under guard: no 500 on empty/missing data, the §4-permitted colour families, and
the showMargin gating on margin metrics.

Rendered with autoescape=True to match the production FastAPI Jinja env.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_phase2_ui_macros.py -v
"""
from __future__ import annotations

import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def jenv():
    # autoescape=True → matches the production FastAPI Jinja2Templates env, so the
    # macros are proven safe/correct under escaping (not just the bare-env case).
    return Environment(loader=FileSystemLoader("app/templates"), autoescape=True)


def _render(jenv, src, **ctx):
    return jenv.from_string(src).render(**ctx)


# ===========================================================================
# customer_flags — Primitive 8 (P2-D2)
# ===========================================================================

IMPORT_FLAGS = "{% from 'macros/customer_flags.html' import customer_flags, customer_flag %}"


def test_customer_flags_renders_known_keys_with_labels_and_colours(jenv):
    html = _render(jenv, IMPORT_FLAGS + "{{ customer_flags(['requires_po','credit_hold','tax_exempt','warranty_escalation']) }}")
    # Labels present — must match constants.CUSTOMER_FLAG_LABELS exactly (no drift)
    for label in ("Requires PO #", "Credit Hold", "Tax Exempt", "Warranty Escalation"):
        assert label in html, f"missing flag label: {label}"
    # §4-permitted colour families, mapped per taxonomy
    assert "bg-amber-100 text-amber-700" in html      # requires_po → warning
    assert "bg-red-100 text-red-700" in html          # credit_hold → danger
    assert "bg-blue-50 text-blue-700" in html         # tax_exempt → info
    assert "bg-purple-50 text-purple-700" in html     # warranty_escalation → special
    # Wrapper + icons
    assert html.count("<svg") == 4
    assert 'class="flex flex-wrap items-center gap-1.5"' in html


def test_customer_flags_compact_hides_label_keeps_icon_and_aria(jenv):
    full = _render(jenv, IMPORT_FLAGS + "{{ customer_flags(['requires_po']) }}")
    compact = _render(jenv, IMPORT_FLAGS + "{{ customer_flags(['requires_po'], compact=True) }}")
    # Full mode shows the visible label span; compact mode drops it...
    assert "<span>Requires PO #</span>" in full
    assert "<span>Requires PO #</span>" not in compact
    # ...but keeps the icon and an accessible label
    assert "<svg" in compact
    assert 'aria-label="Requires PO #"' in compact


def test_customer_flags_empty_or_none_renders_nothing(jenv):
    """The no-500 seam: before Backend builds Customer.flag_keys, callers pass
    [] / None and the macro must emit no markup (not error)."""
    for expr in ("customer_flags([])", "customer_flags(none)", "customer_flags()"):
        html = _render(jenv, IMPORT_FLAGS + "{{ %s }}" % expr)
        assert "<div" not in html and "<span" not in html, f"{expr} should render nothing"
        assert html.strip() == ""


def test_customer_flags_unknown_key_is_defensive_not_fatal(jenv):
    html = _render(jenv, IMPORT_FLAGS + "{{ customer_flags(['totally_made_up']) }}")
    assert "bg-gray-100 text-gray-600" in html            # neutral fallback chip
    assert "Totally Made Up" in html                      # humanised key, visible
    assert "Unregistered flag: totally_made_up" in html   # surfaced in title, not silently dropped


# ===========================================================================
# intelligence_panel + customer/invoice wrappers — Primitive 9 (P2-Q1 / §5.8)
# ===========================================================================

IMPORT_INTEL = ("{% from 'macros/intelligence.html' import "
                "intelligence_panel, customer_intelligence_panel, invoice_intelligence_panel %}")


def test_customer_intelligence_panel_formats_money_and_dates(jenv):
    m = {
        "lifetime_sales": 12340.5, "ytd_sales": 5000.0, "avg_order_value": 257.25,
        "open_ar_balance": 1200.0, "credit_limit": 5000.0, "available_credit": 3800.0,
        "outstanding_core_credits": 150.0, "last_invoice_date": datetime.date(2026, 6, 1),
        "credit_hold": False,
    }
    html = _render(jenv, IMPORT_INTEL + "{{ customer_intelligence_panel(m) }}", m=m)
    assert "Lifetime Sales" in html and "$12340.50" in html   # house money format (no commas)
    assert "Available Credit" in html and "$3800.00" in html
    assert "text-green-700" in html                            # available credit, not on hold → good
    assert "Jun 01, 2026" in html                              # date formatting
    assert "Core Credits" in html


def test_customer_intelligence_panel_credit_hold_tones_red(jenv):
    m = {"available_credit": 0.0, "credit_hold": True}
    html = _render(jenv, IMPORT_INTEL + "{{ customer_intelligence_panel(m) }}", m=m)
    assert "text-red-700" in html
    assert "On credit hold" in html


def test_customer_intelligence_panel_empty_is_safe(jenv):
    """No CustomerMetricsService yet → m=None must render zeros, not 500."""
    html = _render(jenv, IMPORT_INTEL + "{{ customer_intelligence_panel() }}")
    assert "$0.00" in html        # money zeros
    assert "No limit" in html     # credit_limit 0 → "No limit"
    assert "—" in html            # last sale with no date


def test_invoice_intelligence_panel_gates_margin_behind_toggle(jenv):
    """P2-D3: Profit and Margin must be wrapped in x-show='showMargin'; the other
    metrics must not be."""
    m = {"profit": 400.0, "margin_pct": 32.5, "core_liability": 50.0,
         "warranty_exposure": 0.0, "customer_lifetime_sales": 9999.0}
    html = _render(jenv, IMPORT_INTEL + "{{ invoice_intelligence_panel(m) }}", m=m)
    assert "Profit" in html and "Margin" in html
    assert "32.5%" in html
    assert 'x-show="showMargin"' in html                 # gating present
    assert html.count('x-show="showMargin"') == 2        # exactly the 2 margin metrics
    assert "Core Liability" in html                       # non-gated metric still renders


def test_generic_intelligence_panel_empty_renders_nothing(jenv):
    html = _render(jenv, IMPORT_INTEL + "{{ intelligence_panel([]) }}")
    assert html.strip() == ""


# ===========================================================================
# base.html margin toggle — P2-D3 (rendered through the real app stack)
# ===========================================================================

def test_base_html_renders_margin_toggle_through_app():
    """The in-memory engine puts deps.py in test mode (auth bypass), so a real GET
    renders base.html in full — proving the toggle markup + Alpine wiring survive."""
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/customers/")
        assert r.status_code == 200, r.status_code
        body = r.text
        assert "toggleMargin()" in body            # header control wired
        assert "showMargin:" in body               # root x-data state declared
        assert "localStorage.setItem('showMargin'" in body  # persistence


# ===========================================================================
# Taxonomy drift guard — the macro must know every canonical CustomerFlag
# ===========================================================================

def test_customer_flags_macro_covers_every_canonical_flag(jenv):
    """Every value in constants.CustomerFlag must render as a real chip (not the
    gray 'unregistered' fallback), so the macro registry can't silently drift from
    the Backend-owned enum."""
    from app.constants import CustomerFlag
    for flag in CustomerFlag:
        html = _render(jenv, IMPORT_FLAGS + "{{ customer_flag('%s') }}" % flag.value)
        assert "Unregistered flag" not in html, f"macro is missing canonical flag {flag.value!r}"


def test_customer_flag_accepts_strenum_member(jenv):
    """flags_for() yields CustomerFlag members; the macro must handle them (StrEnum
    hashes/compares as its str value) identically to raw key strings."""
    from app.constants import CustomerFlag
    html = _render(jenv, IMPORT_FLAGS + "{{ customer_flags(flags) }}",
                   flags=[CustomerFlag.REQUIRES_PO, CustomerFlag.CREDIT_HOLD])
    assert "Requires PO #" in html and "Credit Hold" in html
    assert "Unregistered flag" not in html


# ===========================================================================
# credit_status — Primitive 10 (P2-D4 / §4.5)
# ===========================================================================

IMPORT_CREDIT = "{% from 'macros/credit_status.html' import credit_badge, credit_warn %}"


def test_credit_badge_states(jenv):
    hold = _render(jenv, IMPORT_CREDIT + "{{ credit_badge(cs) }}",
                   cs={"on_hold": True, "over_limit": False, "available_credit": 500.0,
                       "warn": True, "message": "Customer is on Credit Hold."})
    assert "Credit Hold" in hold and "bg-red-100 text-red-700" in hold

    over = _render(jenv, IMPORT_CREDIT + "{{ credit_badge(cs) }}",
                   cs={"on_hold": False, "over_limit": True, "available_credit": -50.0,
                       "warn": True, "message": "This order exceeds available credit."})
    assert "Over Limit" in over and "bg-amber-100 text-amber-700" in over

    ok = _render(jenv, IMPORT_CREDIT + "{{ credit_badge(cs) }}",
                 cs={"on_hold": False, "over_limit": False, "available_credit": 1500.0, "warn": False})
    assert "$1500.00 credit" in ok and "bg-green-50 text-green-700" in ok

    nolimit = _render(jenv, IMPORT_CREDIT + "{{ credit_badge(cs) }}",
                      cs={"on_hold": False, "over_limit": False, "available_credit": None, "warn": False})
    assert "No Limit" in nolimit and "bg-gray-100 text-gray-600" in nolimit


def test_credit_warn_only_renders_on_warn(jenv):
    none = _render(jenv, IMPORT_CREDIT + "{{ credit_warn(cs) }}",
                   cs={"warn": False, "message": ""})
    assert none.strip() == ""

    empty = _render(jenv, IMPORT_CREDIT + "{{ credit_warn() }}")
    assert empty.strip() == ""

    hold = _render(jenv, IMPORT_CREDIT + "{{ credit_warn(cs) }}",
                   cs={"warn": True, "on_hold": True, "message": "Customer is on Credit Hold."})
    assert "Customer is on Credit Hold." in hold and "bg-red-50 border border-red-200" in hold

    over = _render(jenv, IMPORT_CREDIT + "{{ credit_warn(cs) }}",
                   cs={"warn": True, "on_hold": False, "over_limit": True,
                       "message": "This order exceeds available credit by $250.00."})
    assert "$250.00" in over and "bg-amber-50 border border-amber-200" in over


# ===========================================================================
# metric_strip — Primitive 11 (list-header KPI tiles, §2B / §5.1)
# ===========================================================================

IMPORT_STRIP = "{% from 'macros/metric_strip.html' import metric_strip, metric_tile %}"


def test_metric_strip_renders_tiles(jenv):
    tiles = [
        {"value": "$4,320.00", "label": "Open SO Value", "tone": "brand"},
        {"value": 5, "label": "Backordered", "tone": "amber", "sub": "2 overdue", "alert": True},
        {"value": 0, "label": "On Hold", "tone": "red"},
    ]
    html = _render(jenv, IMPORT_STRIP + "{{ metric_strip(tiles) }}", tiles=tiles)
    assert "Open SO Value" in html and "$4,320.00" in html
    assert "text-2xl" in html                       # KPI big-number treatment
    assert "bg-amber-50" in html and "text-amber-600" in html   # toned tile
    assert "ring-1 ring-red-100" in html            # alert ring
    assert "2 overdue" in html                      # sub-count
    assert "text-gray-400" in html                  # the 0-value tile auto-mutes
    assert 'class="grid grid-cols-2 md:grid-cols-4 gap-3"' in html


def test_metric_strip_tile_href_makes_it_a_link(jenv):
    tiles = [{"value": 3, "label": "Ready to Ship", "tone": "green",
              "href": "/sales-orders/?tab=ready"}]
    html = _render(jenv, IMPORT_STRIP + "{{ metric_strip(tiles) }}", tiles=tiles)
    assert '<a href="/sales-orders/?tab=ready"' in html


def test_metric_strip_empty_renders_nothing(jenv):
    for expr in ("metric_strip([])", "metric_strip(none)", "metric_strip()"):
        html = _render(jenv, IMPORT_STRIP + "{{ %s }}" % expr)
        assert html.strip() == ""


def test_metric_strip_cols_variants_use_compiled_classes(jenv):
    t = [{"value": 1, "label": "X", "tone": "blue"}]
    assert "md:grid-cols-3" in _render(jenv, IMPORT_STRIP + "{{ metric_strip(t, cols=3) }}", t=t)
    assert "md:grid-cols-5" in _render(jenv, IMPORT_STRIP + "{{ metric_strip(t, cols=5) }}", t=t)
    assert "md:grid-cols-4" in _render(jenv, IMPORT_STRIP + "{{ metric_strip(t, cols=4) }}", t=t)


# ===========================================================================
# so_po_status_chip — Primitive 12 (§5.2 backorder / §5.10 SO↔PO link)
# ===========================================================================

IMPORT_SO = "{% from 'macros/so_status.html' import so_po_status_chip %}"


def test_so_chip_renders_backend_rollup_with_link_and_eta(jenv):
    # Shape from SalesOrderMetricsService.po_link_status()
    r = {"status": "ordered", "label": "Ordered", "po_id": 7, "po_number": "PO-2026-0007",
         "eta_date": datetime.date(2026, 6, 9)}
    html = _render(jenv, IMPORT_SO + "{{ so_po_status_chip(r) }}", r=r)
    assert "bg-blue-50 text-blue-700" in html          # in-flight tone
    assert "Ordered" in html and "PO-2026-0007" in html
    assert "ETA Jun 09" in html
    assert '<a href="/purchase-orders/7"' in html      # links to the PO


def test_so_chip_backorder_and_ready_synthetic_states(jenv):
    bo = _render(jenv, IMPORT_SO + "{{ so_po_status_chip({'status': 'backorder'}) }}")
    assert "Backorder" in bo and "bg-amber-50 text-amber-600" in bo
    assert "<a" not in bo                               # no PO → span, not link
    ready = _render(jenv, IMPORT_SO + "{{ so_po_status_chip({'status': 'ready', 'po_id': 3, 'po_number': 'PO-3'}) }}")
    assert "Ready" in ready and "bg-green-50 text-green-700" in ready


def test_so_chip_received_has_no_eta_and_cancelled_is_gray(jenv):
    rec = _render(jenv, IMPORT_SO + "{{ so_po_status_chip(r) }}",
                  r={"status": "received", "label": "Received", "eta_date": datetime.date(2026, 6, 9)})
    assert "Received" in rec and "ETA" not in rec      # arrived → no ETA
    can = _render(jenv, IMPORT_SO + "{{ so_po_status_chip(r) }}",
                  r={"status": "cancelled", "label": "PO Cancelled"})
    assert "PO Cancelled" in can and "bg-gray-100 text-gray-600" in can


def test_so_chip_show_detail_false_drops_po_and_eta(jenv):
    r = {"status": "ordered", "label": "Ordered", "po_id": 7, "po_number": "PO-7",
         "eta_date": datetime.date(2026, 6, 9)}
    html = _render(jenv, IMPORT_SO + "{{ so_po_status_chip(r, show_detail=False) }}", r=r)
    assert "Ordered" in html
    assert "PO-7" not in html and "ETA" not in html    # compact: label only


def test_so_chip_empty_is_safe(jenv):
    for expr in ("so_po_status_chip(none)", "so_po_status_chip({})", "so_po_status_chip()"):
        assert _render(jenv, IMPORT_SO + "{{ %s }}" % expr).strip() == ""


# ===========================================================================
# §5.12 print branding — shared documents/_company_header.html + _footer.html
# (one definition, included by every print template; consumes get_company_dict())
# ===========================================================================

def test_company_header_renders_logo_when_set(jenv):
    html = jenv.get_template("documents/_company_header.html").render(
        company={"name": "JAKS Parts", "logo_url": "/static/uploads/logo.png", "show_logo": True})
    assert '<img src="/static/uploads/logo.png"' in html and 'class="co-logo"' in html
    assert '<div class="co-name">' not in html          # logo replaces the text name


def test_company_header_text_fallback(jenv):
    # (a) no logo_url, (b) logo present but suppressed, (c) bare dict (no branding keys)
    h1 = jenv.get_template("documents/_company_header.html").render(company={"name": "JAKS Parts", "logo_url": None})
    h2 = jenv.get_template("documents/_company_header.html").render(
        company={"name": "JAKS Parts", "logo_url": "/static/x.png", "show_logo": False})
    h3 = jenv.get_template("documents/_company_header.html").render(company={"name": "JAKS Parts"})
    for h in (h1, h2, h3):
        assert '<div class="co-name">JAKS Parts</div>' in h and "<img" not in h


def test_footer_configurable_text_and_fallback(jenv):
    branded = jenv.get_template("documents/_footer.html").render(
        company={"name": "JAKS Parts", "footer_text": "All sales final.\nReturns within 30 days."})
    assert "All sales final." in branded and "Returns within 30 days." in branded
    assert "white-space:pre-line" in branded            # multi-line preserved without |safe
    default = jenv.get_template("documents/_footer.html").render(company={"name": "JAKS Parts", "phone": "555-1212"})
    assert "Thank you for your business" in default and "JAKS Parts" in default


def test_migrated_print_templates_include_shared_branding(jenv):
    """invoices/ + quotes/ print.html now pull the shared header/footer (not inline copies)."""
    import pathlib
    for tpl in ("invoices/print.html", "quotes/print.html"):
        src = pathlib.Path("app/templates", tpl).read_text(encoding="utf-8")
        assert 'include "documents/_company_header.html"' in src, f"{tpl} must include shared header"
        assert 'include "documents/_footer.html"' in src, f"{tpl} must include shared footer"
        assert '<div class="co-name">{{ company.name' not in src, f"{tpl} still has an inline header copy"
        jenv.get_template(tpl)                           # compiles (include refs + syntax valid)


# ===========================================================================
# sortable_th — Primitive 13 (clickable sort column header, §2 lists)
# ===========================================================================

# ===========================================================================
# QBO macros — qbo_status_chip + status_stack + actions_dropdown
# ===========================================================================

IMPORT_CHIPS2 = "{% from 'macros/chips.html' import qbo_status_chip, status_stack, customer_status_chip %}"
IMPORT_ACTIONS = "{% from 'macros/invoice.html' import actions_dropdown %}"


def _inv(overrides=None):
    """Minimal invoice-like object for macro rendering tests."""
    class Inv:
        id = 42
        status = 'open'
        amount_paid = 0.0
        is_locked = True
        lock_reason = 'finalized'
        qbo_sync_status = None
        qbo_invoice_id = None
        qbo_sync_error = None
    o = Inv()
    for k, v in (overrides or {}).items():
        setattr(o, k, v)
    return o


# -- qbo_status_chip ----------------------------------------------------------

# -- customer_status_chip -----------------------------------------------------

def test_customer_status_chip_four_states(jenv):
    for status, expected_color, expected_label in [
        ('active',      'bg-green-50 text-green-700', 'Active'),
        ('inactive',    'bg-gray-100 text-gray-500',  'Inactive'),
        ('on_hold',     'bg-amber-50 text-amber-700', 'On Hold'),
        ('credit_hold', 'bg-red-50 text-red-700',     'Credit Hold'),
    ]:
        h = _render(jenv, IMPORT_CHIPS2 + "{{ customer_status_chip(s) }}", s=status)
        assert expected_color in h, f"{status}: expected color {expected_color}"
        assert expected_label in h, f"{status}: expected label {expected_label}"


def test_customer_status_chip_compact_shows_dot_only(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ customer_status_chip('active', compact=True) }}")
    assert "<span>Active</span>" not in h      # label suppressed in compact mode
    assert "bg-green-500" in h                  # dot still rendered
    assert 'title="Active"' in h                # tooltip present for accessibility


def test_customer_status_chip_unknown_is_defensive(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ customer_status_chip('weird_status') }}")
    assert "bg-gray-100 text-gray-500" in h    # gray fallback
    assert "Weird Status" in h                  # humanised key


def test_qbo_chip_synced(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ qbo_status_chip('synced') }}")
    assert "bg-green-50 text-green-700" in h and "Synced" in h
    # checkmark SVG path
    assert "M5 13l4 4L19 7" in h


def test_qbo_chip_pending(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ qbo_status_chip('pending') }}")
    assert "bg-blue-50 text-blue-700" in h and "Pending" in h


def test_qbo_chip_error(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ qbo_status_chip('error', invoice_id=99) }}")
    assert "bg-red-50 text-red-700" in h and "Failed" in h
    assert "inv #99" in h                            # invoice_id surfaced in tooltip
    assert "<form" not in h and "type=\"submit\"" not in h  # chip is read-only, no form


def test_qbo_chip_not_sent(jenv):
    for s in ('', None, 'skipped'):
        h = _render(jenv, IMPORT_CHIPS2 + "{{ qbo_status_chip(s) }}", s=s)
        assert "bg-green-50" not in h and "bg-blue-50" not in h and "bg-red-50" not in h


def test_qbo_chip_compact_hides_label(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ qbo_status_chip('synced', compact=True) }}")
    assert "<span>Synced</span>" not in h            # label hidden in compact mode
    assert "M5 13l4 4L19 7" in h                     # icon still present


# -- status_stack -------------------------------------------------------------

def test_status_stack_open_locked_shows_posted(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ status_stack(inv) }}", inv=_inv())
    assert "Posted" in h
    # lock tooltip uses human label
    assert "finalized" not in h.lower() or "Locked when finalized" in h


def test_status_stack_paid_shows_paid_badge(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ status_stack(inv) }}",
                inv=_inv({"status": "paid", "lock_reason": "paid"}))
    assert "Posted" in h and "Paid" in h


def test_status_stack_qbo_synced_shows_compact_chip(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ status_stack(inv) }}",
                inv=_inv({"qbo_sync_status": "synced", "qbo_invoice_id": "QB-1"}))
    assert "bg-green-50 text-green-700" in h         # QBO synced chip present
    assert "<span>Synced</span>" not in h             # compact mode (no text label span)


def test_status_stack_void_hides_qbo(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ status_stack(inv) }}",
                inv=_inv({"status": "void", "lock_reason": "voided", "qbo_sync_status": "synced"}))
    assert "QBO" not in h or "Synced" not in h       # QBO chip suppressed for void


def test_status_stack_draft_renders_nothing(jenv):
    h = _render(jenv, IMPORT_CHIPS2 + "{{ status_stack(inv) }}",
                inv=_inv({"status": "draft", "is_locked": False}))
    assert h.strip() == ""


# -- actions_dropdown ---------------------------------------------------------

def test_actions_dropdown_editable_renders_nothing(jenv):
    """DRAFT invoices: dropdown is silent; Save Draft + Finalize are the primary actions."""
    h = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv, editable=True) }}",
                inv=_inv({"status": "draft"}))
    assert "Actions" not in h


def test_actions_dropdown_always_has_print_and_pdf(jenv):
    h = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}", inv=_inv())
    assert "/invoices/42/print" in h and "/invoices/42/pdf" in h
    assert "Actions" in h


def test_actions_dropdown_take_payment_only_open_partial(jenv):
    open_h = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}",
                     inv=_inv({"status": "open"}))
    paid_h = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}",
                     inv=_inv({"status": "paid", "amount_paid": 500.0}))
    assert "Take Payment" in open_h
    assert "Take Payment" not in paid_h


def test_actions_dropdown_warranty_ra_hidden_for_void(jenv):
    void_h = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}",
                     inv=_inv({"status": "void"}))
    assert "Warranty" not in void_h and "Return (RA)" not in void_h


def test_actions_dropdown_qbo_push_label(jenv):
    not_pushed = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}",
                         inv=_inv({"qbo_invoice_id": None, "qbo_sync_status": None}))
    pushed = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}",
                     inv=_inv({"qbo_invoice_id": "QB-99", "qbo_sync_status": "synced"}))
    retry = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}",
                    inv=_inv({"qbo_invoice_id": None, "qbo_sync_status": "error"}))
    assert "Push to QBO" in not_pushed
    assert "Re-Sync QBO" in pushed
    assert "Re-Sync QBO" in retry


def test_actions_dropdown_void_in_menu_not_loose_button(jenv):
    """Void lives inside the dropdown, not as a standalone btn-danger in the header."""
    # With no payments — Void is a danger item in the menu
    h_no_pay = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}",
                       inv=_inv({"status": "open", "amount_paid": 0.0}))
    assert "ctx-item-danger" in h_no_pay
    assert "$dispatch('open-void')" in h_no_pay
    # No loose btn-danger Void button outside the dropdown
    assert "btn-danger" not in h_no_pay


def test_actions_dropdown_void_disabled_when_payments_exist(jenv):
    h = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}",
                inv=_inv({"status": "open", "amount_paid": 250.0}))
    assert "cursor-not-allowed" in h          # disabled void
    assert "ctx-item-danger" not in h         # NOT the danger item (enabled)


def test_actions_dropdown_void_disabled_for_paid(jenv):
    h = _render(jenv, IMPORT_ACTIONS + "{{ actions_dropdown(inv) }}",
                inv=_inv({"status": "paid", "amount_paid": 1000.0}))
    assert "cursor-not-allowed" in h
    assert "$dispatch('open-void')" not in h  # enabled Void dispatch not present


IMPORT_SORTH = "{% from 'macros/sortable.html' import sortable_th %}"


def test_sortable_th_active_asc_shows_up_arrow_and_toggles_to_desc(jenv):
    # href separators render as &amp; under autoescape — assert the param fragments.
    # Param name is `direction` (aligned to Backend apply_sort, NOT `dir`).
    html = _render(jenv, IMPORT_SORTH + "{{ sortable_th('SKU', 'sku', sort='sku', direction='asc') }}")
    assert "SKU" in html
    assert "?sort=sku" in html and "direction=desc" in html  # clicking an asc column flips to desc
    assert "text-brand-600" in html                    # active arrow is branded
    assert "M5 15l7-7 7 7" in html                      # chevron-up (asc)


def test_sortable_th_active_desc_shows_down_arrow_and_toggles_to_asc(jenv):
    html = _render(jenv, IMPORT_SORTH + "{{ sortable_th('SKU', 'sku', sort='sku', direction='desc') }}")
    assert "direction=asc" in html                      # desc → back to asc
    assert "M19 9l-7 7-7-7" in html                     # chevron-down (desc)


def test_sortable_th_inactive_is_neutral_and_links_asc(jenv):
    html = _render(jenv, IMPORT_SORTH + "{{ sortable_th('Vendor', 'vendor', sort='sku', direction='asc') }}")
    assert "?sort=vendor" in html and "direction=asc" in html  # inactive column → asc
    assert "text-gray-300" in html                      # faint neutral indicator
    assert "text-brand-600" not in html                 # not the active column


def test_sortable_th_preserves_qs_and_align(jenv):
    html = _render(jenv, IMPORT_SORTH + "{{ sortable_th('Total', 'total', sort='total', direction='asc', qs='&tab=open&q=acme', align='right') }}")
    for frag in ("sort=total", "direction=desc", "tab=open", "q=acme"):   # other params preserved
        assert frag in html
    assert "text-right" in html
