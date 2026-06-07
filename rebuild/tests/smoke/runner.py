"""
tests/smoke/runner.py
=====================
Drives the 12 business-critical JAKS workflow steps end-to-end in a real
(headless) Chromium browser against an isolated, freshly-seeded app, and writes
a structured pass/fail report (tests/smoke/artifacts/latest.json) that the
/admin/smoke-tests dashboard renders.

Run directly:
    python -m tests.smoke.runner            # headless
    python -m tests.smoke.runner --headed   # watch it
    python -m tests.smoke.runner --keep-db  # don't delete the temp DB (debugging)

Exit code 0 if every step passed (skips allowed), 1 if any step FAILED, 2 on a
bootstrap error (server/browser could not start).

Design
------
* Isolated server + seeded data: see server.py / factories.py.
* Dependency-aware: each step declares ``requires`` (context flags set by earlier
  steps). If a prerequisite is missing (because an upstream step failed), the
  step is SKIPPED — not failed — so the report distinguishes "this broke" from
  "couldn't get here". Independent chains (e.g. the PO→receive→inventory chain)
  still run even if the sales chain broke earlier.
* Honest about real bugs: e.g. the SO product-search endpoint currently 500s
  (Product.name doesn't exist). Step 6 will FAIL and the report shows the actual
  HTTP 500 — but because the sales order inherits the quote's line at conversion,
  steps 7–9 still run. That is the whole point of a smoke suite.
* Failure capture per step: page URL, action, expected, actual, the triggering
  server error (4xx/5xx) seen during the step, plus a screenshot. Trace + video
  are recorded for the whole run.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import tempfile
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Repo root importable as ``app`` (also done in server.py).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.smoke import factories
from tests.smoke import report as R
from tests.smoke.server import start_smoke_server


# ── Context shared across steps ───────────────────────────────────────────────


@dataclass
class SmokeContext:
    page: object                 # playwright Page
    base_url: str
    SessionLocal: object
    seed: factories.SeededData
    run_id: str

    # accumulated workflow state (also act as dependency flags)
    customer_id: int | None = None
    customer_name: str = ""
    quote_id: int | None = None
    quote_has_line: bool = False
    so_id: int | None = None
    so_has_line: bool = False
    invoice_id: int | None = None
    invoice_finalized: bool = False
    po_id: int | None = None
    po_has_line: bool = False
    po_qty: int = 5
    po_received: bool = False

    # inventory baselines
    sale_qoh_before: int | None = None
    recv_qoh_before: int | None = None

    # ring buffer of (status, method, url) for non-2xx responses
    net_errors: deque = field(default_factory=lambda: deque(maxlen=40))

    def note_response(self, response) -> None:
        try:
            if response.status >= 400:
                self.net_errors.append((response.status, response.url))
        except Exception:
            pass


# ── Step descriptor ───────────────────────────────────────────────────────────


@dataclass
class Step:
    key: str
    title: str
    action: str
    expected: str
    requires: tuple[str, ...]
    fn: object  # Callable[[SmokeContext, StepResult], None]


def _url_id(page_url: str, resource: str) -> int:
    m = re.search(rf"/{resource}/(\d+)", page_url)
    if not m:
        raise AssertionError(f"could not parse a /{resource}/<id> from URL: {page_url}")
    return int(m.group(1))


def _probe_status(page, url: str) -> str:
    """Fire a direct GET (shares the browser context's cookies) and return the
    HTTP status as a string — used to report the precise server error behind a
    failed search/typeahead."""
    try:
        resp = page.request.get(url, timeout=8_000)
        return str(resp.status)
    except Exception as exc:  # noqa: BLE001
        return f"probe-failed ({type(exc).__name__})"


def _trunc(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …(truncated)"


# ── The 12 workflow steps ─────────────────────────────────────────────────────
# Each fn drives the REAL UI per the mapped selectors and raises AssertionError
# (or a Playwright error) on failure. On success it sets step.actual to the
# observed result so the report is informative even when green.


def step_create_customer(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    page.goto(f"{ctx.base_url}/customers/new", wait_until="domcontentloaded")
    page.fill('input[name="company_name"]', ctx.customer_name)
    page.fill('input[name="contact_name"]', "Smoke Tester")
    page.fill('input[name="phone"]', "555-0100")
    page.locator('button:has-text("Save Customer")').click()
    page.wait_for_url(re.compile(r"/customers/\d+(\?.*)?$"), timeout=10_000)
    ctx.customer_id = _url_id(page.url, "customers")
    step.actual = f"Created customer #{ctx.customer_id} ({ctx.customer_name}); redirected to {page.url}"


def step_create_quote(ctx: SmokeContext, step) -> None:
    from urllib.parse import quote as urlquote
    from playwright.sync_api import expect

    page = ctx.page
    # Filter the customer list to our unique customer so its row is unambiguous,
    # then click that row's one-click "+ Quote" (a native form POST → /quotes/{id}).
    page.goto(f"{ctx.base_url}/customers/?q={urlquote(ctx.customer_name)}", wait_until="domcontentloaded")
    row = page.locator(f'tr:has(a[href="/customers/{ctx.customer_id}"])')
    expect(row).to_be_visible(timeout=8_000)
    row.locator('button:has-text("+ Quote")').first.click()
    page.wait_for_url(re.compile(r"/quotes/\d+(\?.*)?$"), timeout=10_000)
    ctx.quote_id = _url_id(page.url, "quotes")
    step.actual = f"Created quote #{ctx.quote_id} for customer #{ctx.customer_id}; landed on {page.url}"


def step_add_product_to_quote(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    page.goto(f"{ctx.base_url}/quotes/{ctx.quote_id}", wait_until="domcontentloaded")
    # §8H shared line-adder (line_items/_line_adder.html): fill search → wait for the
    # first result → set qty → CLICK the result to add IMMEDIATELY (no "+ Add Line").
    search = page.locator("input[x-ref='searchInput']")
    expect(search).to_be_visible(timeout=8_000)
    rows = page.locator("#quote-lines-tbody tr[id^='line-']")
    before = rows.count()
    search.fill(ctx.seed.sale_sku)
    # Alpine + fetch JSON search (not HTMX): wait for the first result button.
    expect(page.locator("#la-result-0")).to_be_visible(timeout=8_000)
    qty = page.locator("input[x-ref='qtyInput']")
    if qty.count():
        qty.fill("2")
    page.locator("#la-result-0").click()  # one-click immediate add → POST {product_id, qty}
    expect(rows).to_have_count(before + 1, timeout=8_000)
    ctx.quote_has_line = True
    step.actual = f"Added product {ctx.seed.sale_sku} to quote #{ctx.quote_id}; line rows {before}→{before + 1}"


def step_save_quote(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    page.goto(f"{ctx.base_url}/quotes/{ctx.quote_id}", wait_until="domcontentloaded")
    # Scope to the quote header form — base.html's global Log Call panel also has
    # a textarea[name="notes"], so an unscoped selector is ambiguous.
    notes = page.locator("#quote-header-form textarea[name='notes']")
    expect(notes).to_be_visible(timeout=8_000)
    notes.fill(f"Smoke autosave check {ctx.run_id}")
    # Save Standard v2 (workspace.html sticky save bar): editing a #quote-header-form
    # field flips the pill to 'Unsaved changes' (dirty) and schedules a debounced
    # POST /quotes/{id}/autosave. The manual 'Save' button force-flushes and, on
    # success, surfaces the distinct green 'Quote saved' toast. (The old
    # #autosave-status / 'All changes saved' element was removed in this rework.)
    expect(page.get_by_text("Unsaved changes")).to_be_visible(timeout=5_000)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Quote saved")).to_be_visible(timeout=10_000)
    step.actual = (
        "Header autosave confirmed — editing notes flipped the save pill to "
        "'Unsaved changes', and the manual Save flushed to a 'Quote saved' toast."
    )


def step_convert_quote_to_so(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    page.goto(f"{ctx.base_url}/quotes/{ctx.quote_id}", wait_until="domcontentloaded")
    # Open the "→ Sales Order ▾" popover (Alpine toggle; <button>, sidebar nav is <a>).
    page.locator('button:has-text("Sales Order")').first.click()
    create = page.locator('button:has-text("Create Sales Order")')
    expect(create).to_be_visible(timeout=5_000)
    # payment_mode defaults to 'none' (pre-checked) — submit directly.
    create.click()
    page.wait_for_url(re.compile(r"/sales-orders/\d+(\?.*)?$"), timeout=10_000)
    ctx.so_id = _url_id(page.url, "sales-orders")
    ctx.so_has_line = ctx.quote_has_line  # SO inherits the quote's lines at conversion
    step.actual = f"Converted quote #{ctx.quote_id} → sales order #{ctx.so_id}; landed on {page.url}"


def step_add_product_to_so(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    page.goto(f"{ctx.base_url}/sales-orders/{ctx.so_id}", wait_until="domcontentloaded")
    # §8H shared line-adder — a sibling ABOVE #so-lines-section (it lives OUTSIDE the
    # swapped region, so it survives the add-line outerHTML swap). This is the same
    # Alpine fetch component as the quote adder, so the typeahead fires under
    # automation on fill() — the old declarative hx-trigger fallback is gone.
    # Fill search → wait for the first result → CLICK to add IMMEDIATELY.
    search = page.locator("input[x-ref='searchInput']")
    expect(search).to_be_visible(timeout=8_000)
    rows = page.locator("#so-lines-section table.tbl tbody tr")
    before = rows.count()
    search.fill(ctx.seed.sale_sku)
    expect(page.locator("#la-result-0")).to_be_visible(timeout=8_000)
    page.locator("#la-result-0").click()  # one-click immediate add → POST {product_id, qty}
    expect(rows).to_have_count(before + 1, timeout=8_000)
    step.actual = f"Added product {ctx.seed.sale_sku} to sales order #{ctx.so_id}; line rows {before}→{before + 1}"


def step_convert_so_to_invoice(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    # Inventory decrements at fulfillment (fulfill_and_invoice → finalise), so
    # baseline the SALE product's on-hand NOW, before the conversion.
    ctx.sale_qoh_before = factories.get_qty_on_hand(ctx.SessionLocal, ctx.seed.sale_product_id)
    page.goto(f"{ctx.base_url}/sales-orders/{ctx.so_id}", wait_until="domcontentloaded")
    fulfill = page.locator('#fulfill-form button[type="submit"]')
    expect(fulfill).to_be_visible(timeout=8_000)
    # A native confirm() fires before the POST; the global dialog handler accepts it.
    fulfill.click()
    page.wait_for_url(re.compile(r"/invoices/\d+(\?.*)?$"), timeout=12_000)
    ctx.invoice_id = _url_id(page.url, "invoices")
    step.actual = f"Fulfilled sales order #{ctx.so_id} → invoice #{ctx.invoice_id}; landed on {page.url}"


def step_finalize_invoice(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    # An SO-fulfilled invoice is AUTO-finalized to OPEN by fulfill_and_invoice()
    # (it calls InvoiceService.finalise internally). A directly-created invoice is
    # DRAFT and uses the manual Finalize modal. Handle both honestly.
    status, _ = _invoice_payment_state(ctx.SessionLocal, ctx.invoice_id)
    page.goto(f"{ctx.base_url}/invoices/{ctx.invoice_id}", wait_until="domcontentloaded")

    if status == "draft":
        # Manual finalize: header 'Finalize' (first) opens the confirm modal; the
        # modal's submit posts /invoices/{id}/finalise.
        page.locator('button:has-text("Finalize")').first.click()
        dialog = page.locator('div[role="dialog"][aria-labelledby="finalize-modal-title"]')
        expect(dialog).to_be_visible(timeout=5_000)
        dialog.locator('button[type="submit"]').click()
        page.wait_for_url(re.compile(r"/invoices/\d+\?.*"), timeout=10_000)
        if "error=" in page.url:
            from urllib.parse import unquote
            raise AssertionError(f"finalize redirected with error: {unquote(page.url.split('error=', 1)[1])}")
        if "ok=" not in page.url:
            raise AssertionError(f"expected ?ok= success redirect, got {page.url}")
        new_status, _ = _invoice_payment_state(ctx.SessionLocal, ctx.invoice_id)
        ctx.invoice_finalized = True
        step.actual = f"Finalized DRAFT invoice #{ctx.invoice_id} via the confirm modal → status {new_status.upper()}"
    elif status in ("open", "partial"):
        # Already finalized at fulfillment. Confirm the finalized UI (Take Payment shows).
        expect(page.locator('button:has-text("Take Payment")')).to_be_visible(timeout=5_000)
        ctx.invoice_finalized = True
        step.actual = (
            f"Invoice #{ctx.invoice_id} was auto-finalized at SO fulfillment (status {status.upper()}); "
            f"no manual finalize step exists for SO-derived invoices. Finalized state confirmed "
            f"('Take Payment' available)."
        )
    elif status == "paid":
        ctx.invoice_finalized = True
        step.actual = f"Invoice #{ctx.invoice_id} already finalized and PAID."
    else:
        raise AssertionError(f"invoice #{ctx.invoice_id} in unexpected status '{status}' — cannot finalize")


def step_record_payment(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    page.goto(f"{ctx.base_url}/invoices/{ctx.invoice_id}", wait_until="domcontentloaded")
    page.locator('button:has-text("Take Payment")').click()
    amount = page.locator('form[action$="/payment"] input[name="amount"]')
    expect(amount).to_be_visible(timeout=5_000)
    # Amount is pre-filled to the full balance due — leave it to pay in full.
    page.locator('form[action$="/payment"] button[type="submit"]:has-text("Record Payment")').click()
    page.wait_for_url(re.compile(r"/invoices/\d+(\?.*)?$"), timeout=10_000)
    status, paid = _invoice_payment_state(ctx.SessionLocal, ctx.invoice_id)
    if status not in ("paid", "partial") or paid <= 0:
        raise AssertionError(f"payment did not register: invoice status={status}, amount_paid={paid}")
    step.actual = f"Recorded payment on invoice #{ctx.invoice_id}: status={status}, amount_paid={paid:.2f}"


def step_create_po(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    page.goto(f"{ctx.base_url}/purchase-orders/", wait_until="domcontentloaded")
    page.locator('button:has-text("New PO")').first.click()
    vendor = page.locator('#create-slide-content select[name="vendor_id"]')
    expect(vendor).to_be_visible(timeout=8_000)
    vendor.select_option(str(ctx.seed.vendor_id))
    page.locator('#create-slide-content button[type="submit"]:has-text("Create PO")').click()
    page.wait_for_url(re.compile(r"/purchase-orders/\d+(\?.*)?$"), timeout=10_000)
    ctx.po_id = _url_id(page.url, "purchase-orders")
    # Stage B: add a product line via the §8H shared line-adder (mode=cost) — a
    # sibling ABOVE #po-lines-section. Fill search → wait for the first result →
    # set qty → CLICK to add IMMEDIATELY. unit_cost auto-fills from the product
    # (no manual fill); there is no "+ Add" button anymore.
    expect(page.locator("#po-lines-section")).to_be_visible(timeout=8_000)
    sku_search = page.locator("input[x-ref='searchInput']")
    expect(sku_search).to_be_visible(timeout=8_000)
    sku_search.fill(ctx.seed.recv_sku)
    expect(page.locator("#la-result-0")).to_be_visible(timeout=8_000)
    qty = page.locator("input[x-ref='qtyInput']")
    if qty.count():
        qty.fill(str(ctx.po_qty))
    page.locator("#la-result-0").click()  # one-click immediate add → POST {product_id, qty}
    expect(page.locator('#po-lines-section table tbody tr td a[href^="/products/"]').first).to_be_visible(timeout=8_000)
    ctx.po_has_line = True
    step.actual = f"Created PO #{ctx.po_id}, added {ctx.po_qty}× {ctx.seed.recv_sku}"


def step_receive_po(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    page.goto(f"{ctx.base_url}/purchase-orders/{ctx.po_id}", wait_until="domcontentloaded")
    # A fresh PO is DRAFT — must be sent to vendor before it is receivable.
    page.locator('form[action$="/send"] button[type="submit"]').first.click()
    page.wait_for_load_state("domcontentloaded")
    receive = page.locator("#receive")
    expect(receive).to_be_visible(timeout=10_000)
    # Baseline RECEIVE product on-hand right before receiving.
    ctx.recv_qoh_before = factories.get_qty_on_hand(ctx.SessionLocal, ctx.seed.recv_product_id)
    recv_input = receive.locator('input[name^="recv_"]').first
    expect(recv_input).to_be_visible(timeout=5_000)
    recv_input.fill(str(ctx.po_qty))
    # Check the submit button is actually associated with the receive <form>. A
    # nested per-line cancel <form> can close the receive form early (nested forms
    # are invalid HTML), orphaning this button so the POST never fires.
    orphaned = page.evaluate(
        "() => { const b = document.querySelector('#receive button[type=submit]');"
        " return b ? (b.form === null) : null; }"
    )
    receive.locator('button[type="submit"]:has-text("Receive")').first.click()
    try:
        page.wait_for_url(re.compile(r"/purchase-orders/\d+\?ok=received"), timeout=10_000)
    except Exception:
        if orphaned:
            raise AssertionError(
                "Receive submit button is ORPHANED from its <form> (button.form is null) — a nested "
                "per-line cancel <form> inside the receive <form> closes it early, so 'Receive & Update "
                f"Inventory' submits nothing. No receipt is recorded. URL stayed at {page.url}"
            )
        raise AssertionError(f"receive did not redirect to ?ok=received; URL stayed at {page.url}")
    ctx.po_received = True
    step.actual = f"Sent + received PO #{ctx.po_id} ({ctx.po_qty} units); redirect {page.url}"


def step_confirm_inventory(ctx: SmokeContext, step) -> None:
    from playwright.sync_api import expect

    page = ctx.page
    notes = []
    ui_product_id = None  # which product to confirm in the UI

    # ── Decrement: SALE product, at invoice fulfillment/finalize ──
    if ctx.invoice_finalized and ctx.sale_qoh_before is not None and ctx.invoice_id:
        sale_after = factories.get_qty_on_hand(ctx.SessionLocal, ctx.seed.sale_product_id)
        invoiced = factories.sum_invoice_product_qty(
            ctx.SessionLocal, ctx.invoice_id, ctx.seed.sale_product_id
        )
        expected_sale = max(0, ctx.sale_qoh_before - invoiced)
        if sale_after != expected_sale:
            raise AssertionError(
                f"sale product {ctx.seed.sale_sku} on-hand expected {expected_sale} "
                f"({ctx.sale_qoh_before}−{invoiced}), got {sale_after}"
            )
        notes.append(f"{ctx.seed.sale_sku}: {ctx.sale_qoh_before} → {sale_after} (−{invoiced}) at invoice finalize")
        ui_product_id = ctx.seed.sale_product_id

    # ── Increment: RECEIVE product, at PO receipt ──
    if ctx.po_received and ctx.recv_qoh_before is not None:
        recv_after = factories.get_qty_on_hand(ctx.SessionLocal, ctx.seed.recv_product_id)
        expected = ctx.recv_qoh_before + ctx.po_qty
        if recv_after != expected:
            raise AssertionError(
                f"receive product {ctx.seed.recv_sku} on-hand expected {expected} "
                f"({ctx.recv_qoh_before}+{ctx.po_qty}), got {recv_after}"
            )
        notes.append(f"{ctx.seed.recv_sku}: {ctx.recv_qoh_before} → {recv_after} (+{ctx.po_qty}) at PO receive")
        ui_product_id = ctx.seed.recv_product_id  # prefer the receive product for the UI check

    if not notes:
        raise AssertionError(
            "No completed inventory-changing step to verify — both the invoice finalize "
            "and the PO receive failed upstream, so there is no quantity change to confirm."
        )

    # ── Confirm the change is reflected in the product-detail UI (not just the DB) ──
    db_qty = factories.get_qty_on_hand(ctx.SessionLocal, ui_product_id)
    page.goto(f"{ctx.base_url}/products/{ui_product_id}", wait_until="domcontentloaded")
    # 'On Hand' lives in the Stock Levels card on the product-detail INVENTORY tab
    # (x-show="tab==='inventory'"); the page opens on the Info tab, so activate the
    # Inventory tab first or the cell stays hidden (display:none).
    page.locator('nav.tab-bar button:has-text("Inventory")').click()
    on_hand_cell = page.locator(
        "xpath=//label[normalize-space(.)='On Hand']/following-sibling::div[1]"
    ).first
    expect(on_hand_cell).to_be_visible(timeout=8_000)
    ui_text = on_hand_cell.inner_text().strip()
    ui_val = int(re.sub(r"[^0-9-]", "", ui_text) or "-999999")
    if ui_val != db_qty:
        raise AssertionError(f"product #{ui_product_id} detail UI shows On Hand={ui_text!r} but DB says {db_qty}")
    notes.append(f"product-detail UI On Hand reads {ui_val} (matches DB)")

    step.actual = "; ".join(notes)


STEPS: list[Step] = [
    Step("create_customer", "Create customer",
         "Submit the New Customer form (POST /customers/new)",
         "303 redirect to /customers/{id}",
         (), step_create_customer),
    Step("create_quote", "Create quote from customer",
         "Click '+ Quote' on the customer's list row (POST /quotes/new)",
         "303 redirect to /quotes/{id} (new draft quote)",
         ("customer_id",), step_create_quote),
    Step("add_product_to_quote", "Add product to quote (product search)",
         "Search the quote line-adder; click the first result (#la-result-0) to add it immediately",
         "A new line row appears in #quote-lines-tbody",
         ("quote_id",), step_add_product_to_quote),
    Step("save_quote", "Save quote",
         "Edit the quote notes (save bar flips to 'Unsaved changes'), then click Save",
         "Manual Save force-flushes the autosave → a 'Quote saved' toast appears",
         ("quote_id",), step_save_quote),
    Step("convert_quote_to_so", "Convert quote to sales order",
         "Open '→ Sales Order' popover and click 'Create Sales Order'",
         "303 redirect to /sales-orders/{id}",
         ("quote_id",), step_convert_quote_to_so),
    Step("add_product_to_so", "Add product to sales order (product search)",
         "Search the SO line-adder; click the first result (#la-result-0) to add it immediately",
         "A new line row appears in #so-lines-section",
         ("so_id",), step_add_product_to_so),
    Step("convert_so_to_invoice", "Convert sales order to invoice",
         "Click 'Fulfill & Invoice →' (accept the confirm dialog)",
         "303 redirect to /invoices/{id}",
         ("so_id", "so_has_line"), step_convert_so_to_invoice),
    Step("finalize_invoice", "Finalize invoice",
         "Finalize the invoice (manual modal if DRAFT; SO-fulfilled invoices are auto-finalized)",
         "Invoice is in finalized/OPEN state",
         ("invoice_id",), step_finalize_invoice),
    Step("record_payment", "Record payment",
         "Open 'Take Payment' and submit 'Record Payment' for the full balance",
         "Invoice status becomes PAID/PARTIAL with amount_paid > 0",
         ("invoice_id", "invoice_finalized"), step_record_payment),
    Step("create_po", "Create purchase order",
         "Open 'New PO', pick the vendor, create, then add a product line",
         "PO created (/purchase-orders/{id}) with one line",
         (), step_create_po),
    Step("receive_po", "Receive purchase order",
         "Send the PO to vendor, then 'Receive & Update Inventory'",
         "Redirect to /purchase-orders/{id}?ok=received",
         ("po_id", "po_has_line"), step_receive_po),
    Step("confirm_inventory", "Confirm inventory quantity changes",
         "Re-read on-hand (DB + product-detail UI) after finalize and/or receive",
         "On-hand reflects the sale decrement and/or receive increment; UI matches DB",
         (), step_confirm_inventory),
]


# ── DB read helpers (truth source for status assertions) ──────────────────────


def _invoice_payment_state(SessionLocal, invoice_id: int) -> tuple[str, float]:
    from app.models.invoice import Invoice

    db = SessionLocal()
    try:
        inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if inv is None:
            return ("missing", 0.0)
        try:
            paid = float(inv.amount_paid)
        except Exception:
            paid = 0.0
        return (str(inv.status), paid)
    finally:
        db.close()


# ── Driver ────────────────────────────────────────────────────────────────────


def _missing_requirement(ctx: SmokeContext, requires: tuple[str, ...]) -> str | None:
    for attr in requires:
        if not getattr(ctx, attr, None):
            return attr
    return None


def _login(page, base_url: str) -> None:
    """Authenticate the smoke browser session once, before the workflow steps.

    Since O2 enforcement landed there are TWO independent auth gates:
      1. app.main `enforce_login` middleware — bypassed by JAKS_SKIP_AUTH (conftest
         sets it for the in-memory TestClient suite).
      2. app.deps.get_current_user_id — bypassed ONLY when the engine URL contains
         ':memory:' (see ``_is_test_env``).
    The smoke server runs against a real FILE SQLite DB on purpose (so the runner
    can read inventory back), so gate #2 stays ARMED — every protected route 302s
    to /login until the browser holds a valid signed session cookie. So we sign in
    through the real form exactly as a user would; the cookie then rides every
    subsequent request, including the same-origin JSON product-search ``fetch``.

    The admin/admin user is seeded at startup by app.main._seed_default_user
    (password overridable via JAKS_ADMIN_PASSWORD). A failure HERE is a harness /
    auth-change problem, not a product bug — raise so the suite is marked a
    bootstrap ERROR with a clear message rather than every step FAILing or
    SKIPping confusingly downstream.
    """
    password = os.environ.get("JAKS_ADMIN_PASSWORD", "admin")
    try:
        page.goto(f"{base_url}/login", wait_until="domcontentloaded")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', password)
        page.locator('form[action="/login"] button[type="submit"]').click()
        # POST /login → 303 to / on success, or back to /login?error=1 on failure.
        # Wait until we have navigated AWAY from the login page.
        page.wait_for_url(lambda url: "/login" not in url, timeout=10_000)
    except Exception as exc:  # noqa: BLE001 — reframe as an explicit bootstrap error
        raise RuntimeError(
            "Smoke harness could not authenticate the browser session via POST /login "
            "(admin / JAKS_ADMIN_PASSWORD). Every protected route 302s to /login until "
            "this succeeds, so no workflow step can run. "
            f"Underlying error: {type(exc).__name__}: {exc}. Landed on: {page.url}. "
            "Verify app/routers/auth.py (Form fields 'username'/'password'), "
            "app/templates/auth/login.html (matching input names + submit), and "
            "app/main._seed_default_user (the admin user must be seeded)."
        ) from exc


def run_suite(*, headed: bool = False, keep_db: bool = False) -> R.SuiteResult:
    from playwright.sync_api import sync_playwright

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"run-{ts}"
    run_dir = R.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    video_dir = run_dir / "video"

    suite = R.SuiteResult(
        status=R.RUNNING,
        started_at=datetime.now(timezone.utc).isoformat(),
        artifacts_dir=_rel(run_dir),
        environment=_environment(),
    )
    # Pre-create step rows so the dashboard shows the full checklist immediately.
    suite.steps = [
        R.StepResult(key=s.key, index=i + 1, title=s.title, action=s.action, expected=s.expected)
        for i, s in enumerate(STEPS)
    ]

    t0 = time.monotonic()
    db_dir = pathlib.Path(tempfile.mkdtemp(prefix="jaks_smoke_"))
    db_path = db_dir / "jaks_smoke.db"
    server = None

    try:
        server = start_smoke_server(db_path)
        suite.base_url = server.base_url
        seed = factories.seed(server.SessionLocal)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(video_dir),
            )
            context.set_default_timeout(10_000)
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            # Accept the native confirm() in the SO fulfill step (and any other).
            page.on("dialog", lambda d: d.accept())

            # One-time auth BEFORE any step: O2 enforcement 302s every protected
            # route to /login unless the browser holds a signed session cookie, and
            # the smoke server's FILE DB keeps app.deps' auth gate armed. Sign in
            # once here so the cookie rides every subsequent request. A failure
            # raises → caught by the outer handler → suite marked a bootstrap ERROR.
            _login(page, server.base_url)

            ctx = SmokeContext(
                page=page,
                base_url=server.base_url,
                SessionLocal=server.SessionLocal,
                seed=seed,
                run_id=ts,
                customer_name=f"Smoke Diesel {ts}",  # unique → dodges duplicate-detection
            )
            page.on("response", ctx.note_response)

            for i, spec in enumerate(STEPS):
                sr = suite.steps[i]
                missing = _missing_requirement(ctx, spec.requires)
                if missing:
                    sr.status = R.SKIPPED
                    sr.actual = f"Skipped — prerequisite not met (upstream step set no '{missing}')."
                    continue

                err_mark = len(ctx.net_errors)
                step_t0 = time.monotonic()
                try:
                    sr.page_url = page.url
                    spec.fn(ctx, sr)
                    sr.status = R.PASSED
                    sr.page_url = page.url
                except Exception as exc:  # noqa: BLE001 — record every failure
                    sr.status = R.FAILED
                    sr.page_url = page.url
                    sr.error = _trunc(f"{type(exc).__name__}: {exc}".strip(), 700)
                    server_errs = list(ctx.net_errors)[err_mark:]
                    if not sr.actual:
                        sr.actual = sr.error
                    else:
                        sr.actual = _trunc(sr.actual, 700)
                    if server_errs:
                        tail = "; ".join(f"HTTP {s} {u}" for s, u in server_errs[-4:])
                        sr.actual = f"{sr.actual}  [server errors during step: {tail}]"
                    sr.screenshot = _safe_screenshot(page, run_dir, i + 1, spec.key)
                finally:
                    sr.duration_s = round(time.monotonic() - step_t0, 2)

            # Trace + video for the whole run.
            trace_path = run_dir / "trace.zip"
            try:
                context.tracing.stop(path=str(trace_path))
                suite.trace = _rel(trace_path)
            except Exception:
                suite.trace = None
            vid = page.video
            page.close()
            context.close()
            browser.close()
            try:
                if vid is not None:
                    suite.video_dir = _rel(pathlib.Path(vid.path()).parent)
                    # attach the video path to the first failed step for convenience
                    _attach_video(suite, _rel(pathlib.Path(vid.path())))
            except Exception:
                suite.video_dir = _rel(video_dir)

        suite.status = R.FAILED if any(s.status == R.FAILED for s in suite.steps) else R.PASSED

    except Exception as exc:  # bootstrap failure (server/browser/seed)
        suite.status = R.ERROR
        suite.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    finally:
        if server is not None:
            server.stop()
        suite.duration_s = round(time.monotonic() - t0, 2)
        suite.finished_at = datetime.now(timezone.utc).isoformat()
        # Persist: latest.json + a timestamped history copy.
        R.write_results(suite)
        R.write_results(suite, run_dir / "result.json")
        if not keep_db:
            _rmtree(db_dir)

    return suite


# ── small utilities ───────────────────────────────────────────────────────────


def _rel(path: pathlib.Path) -> str:
    """Path relative to ARTIFACTS_DIR, forward-slashed (for the admin artifact route)."""
    try:
        return str(pathlib.Path(path).resolve().relative_to(R.ARTIFACTS_DIR.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _safe_screenshot(page, run_dir: pathlib.Path, index: int, key: str) -> str | None:
    try:
        path = run_dir / f"{index:02d}_{key}.png"
        page.screenshot(path=str(path), full_page=True)
        return _rel(path)
    except Exception:
        return None


def _attach_video(suite: R.SuiteResult, rel_video: str) -> None:
    for s in suite.steps:
        if s.status == R.FAILED and not s.video:
            s.video = rel_video


def _environment() -> dict:
    env = {"python": sys.version.split()[0]}
    try:
        from importlib.metadata import version
        env["playwright"] = version("playwright")
    except Exception:
        env["playwright"] = "unknown"
    return env


def _rmtree(path: pathlib.Path) -> None:
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the JAKS workflow smoke suite.")
    parser.add_argument("--headed", action="store_true", help="run with a visible browser")
    parser.add_argument("--keep-db", action="store_true", help="keep the temp SQLite DB for debugging")
    args = parser.parse_args()

    # Windows consoles default to cp1252 — UI strings contain glyphs like ↵/→/▾.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    R.set_running(True)
    try:
        suite = run_suite(headed=args.headed, keep_db=args.keep_db)
    finally:
        R.set_running(False)

    t = suite.totals
    print(f"\nSmoke suite: {suite.status.upper()}  "
          f"({t.get('passed', 0)} passed / {t.get('failed', 0)} failed / {t.get('skipped', 0)} skipped of {t['total']})")
    for s in suite.steps:
        mark = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(s.status, s.status.upper())
        print(f"  [{mark}] {s.index:>2}. {s.title}")
        if s.status == R.FAILED:
            print(f"         action  : {s.action}")
            print(f"         expected: {s.expected}")
            print(f"         actual  : {s.actual}")
            print(f"         url     : {s.page_url}")
            if s.screenshot:
                print(f"         shot    : {s.screenshot}")
    if suite.status == R.ERROR:
        print(f"\nBOOTSTRAP ERROR:\n{suite.error}")
        return 2
    return 0 if suite.status == R.PASSED else 1


if __name__ == "__main__":
    raise SystemExit(main())
