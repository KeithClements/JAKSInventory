"""
scripts/qbo_sandbox_smoke.py
============================
End-to-end QuickBooks Online SANDBOX smoke test for the JAKS ERP → QBO push path.

WHAT IT PROVES
--------------
Once the owner has connected the ERP to a QuickBooks Online *sandbox* company
(Settings → QuickBooks → Connect, with qbo_environment = "sandbox") and clicked
"Set up QBO items", this script drives one of EACH synced document type through
the NORMAL ERP service layer, pushes each to QBO using the very same
QBOSyncService methods the app uses, then reads each document back out of QBO and
asserts the account/item mapping is what we expect:

  1. Invoice  — a PRODUCT line + a CORE_CHARGE line + a FREIGHT line, so one push
     exercises all three account mappings at once:
       • parts   → "JAKS Parts Sales"      (Income)
       • core    → "JAKS Core Charge"      (pass-through liability, if mapped)
       • freight → "JAKS Freight & Delivery"
     cc_surcharge and tax lines are (correctly) NOT pushed as invoice lines.
  2. Payment  — a card payment against that invoice, WITH a card surcharge, so it
     verifies both the QBO Payment (LinkedTxn back to the invoice) AND the
     separate one-line SalesReceipt ("JAKS Card Surcharge") that books the
     surcharge so the QBO deposit reconciles against the bank.
  3. Vendor bill — an APPROVED bill (with a freight line) built from a real PO +
     receipt, pushed as a QBO Bill (AccountBasedExpenseLine → COGS account, plus
     a freight-in expense line).
  4. Credit memo — a credit memo against the invoice, pushed as a QBO CreditMemo,
     with the credited core line mapped back to "JAKS Core Charge".

For each pushed document the script re-queries QBO by the returned id and checks
the line items map to the expected QBO Item / account. It prints a per-check
PASS/FAIL table and a final summary, and exits non-zero if ANY check failed.

THE SAFETY GATE (read this)
---------------------------
This script REFUSES to run unless QBOClient reports environment == "sandbox".
It is impossible to accidentally run it against a production QuickBooks company:
if the connected environment is "production" it prints an error and exits 2
BEFORE any customer/vendor/document is created or any push happens. There is no
override flag — point the ERP at a sandbox company or it will not run.

All the smoke records are named/prefixed "ZZ SMOKE TEST" so they are trivial to
find and delete in the sandbox. Re-runs are idempotent-ish: documents carry a
deterministic marker/DocNumber suffix derived from --run-tag (default "SMOKE"),
so the same tag reuses the same local smoke customer/vendor/products instead of
piling up new ones. Use a fresh --run-tag (or --cleanup first) for a clean slate.

HOW TO RUN
----------
Preconditions (one time, done in the app UI by the owner):
  1. Settings → QuickBooks: enter Client ID/Secret, set Environment = Sandbox,
     Connect (OAuth) to the Intuit *sandbox* company.
  2. Settings → QuickBooks: click "Set up QBO items" (creates the generic items).
  3. (optional but recommended) Settings → QBO Accounts: map the Core Charge
     liability + freight accounts so cores/freight post to the right place.

Then, with the server STOPPED (so nothing else writes the DB) from the repo root:

    # dry preflight only — checks connection, sandbox gate, items, accounts:
    .venv\\Scripts\\python.exe -m scripts.qbo_sandbox_smoke --check-only

    # full run — creates + pushes + verifies one of each document type:
    .venv\\Scripts\\python.exe -m scripts.qbo_sandbox_smoke

    # verify only the environment safety gate wiring, ZERO network/DB writes:
    .venv\\Scripts\\python.exe -m scripts.qbo_sandbox_smoke --self-check

    # remove the ZZ SMOKE TEST docs afterwards (voids in QBO where the API
    # allows; always removes the local records for the run tag):
    .venv\\Scripts\\python.exe -m scripts.qbo_sandbox_smoke --cleanup

Options:
    --db PATH        run against an alternate SQLite file (sets JAKS_DB_PATH).
    --run-tag TAG    marker suffix for this run's records (default "SMOKE").
    --check-only     run the preconditions checks and exit (no writes/pushes).
    --self-check     unit-check the sandbox/production gate with a monkeypatched
                     environment flag; makes NO network or DB calls. Exits 0 on
                     pass, non-zero on failure.
    --cleanup        void/delete this run's smoke docs in QBO where possible and
                     delete the local ZZ SMOKE TEST records, then exit.

READING THE OUTPUT
------------------
Each phase prints "[ PASS ]" / "[ FAIL ]" lines. A PASS means the document landed
in QBO AND, on read-back, its lines mapped to the expected item/account. The final
"SUMMARY" block totals passes/fails; the process exit code is 0 only when every
check passed. A FAIL line quotes what was expected vs what QBO returned so you can
open that document in the sandbox and see the discrepancy.

CONVENTIONS
-----------
In-process against data/jaks.db via app.database.SessionLocal (same bootstrap the
other scripts/ files use) — no HTTP, cookie, or CSRF. Pushes go through
QBOSyncService(db, current_user_id=None) — the system actor, which passes the
REPUSH_QBO gate by design (same as the background retry worker). Read-backs use
QBOClient.query(...), the same client the push uses. Fail-soft everywhere: a QBO
error is reported as a FAIL check, never an unhandled traceback.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ── bootstrap: repo root on sys.path (mirrors scripts/import_and_push.py) ───────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# UTF-8 stdout so a cp1252 console never crashes on the ✓/✗-free output we print.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

log = logging.getLogger("qbo_sandbox_smoke")

# Prefix every smoke record so they are easy to find + delete in the sandbox.
SMOKE_PREFIX = "ZZ SMOKE TEST"


# ── result plumbing ─────────────────────────────────────────────────────────────

@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, ok, detail))
        status = "[ PASS ]" if ok else "[ FAIL ]"
        line = f"  {status} {name}"
        if detail:
            line += f" — {detail}"
        print(line, flush=True)
        return ok

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.ok)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.ok)


# ── the environment SAFETY GATE (unit-checkable in isolation) ────────────────────

class ProductionEnvironmentError(RuntimeError):
    """Raised when the connected QBO company is not a sandbox."""


def assert_sandbox(environment: str) -> None:
    """HARD safety gate. Only 'sandbox' is allowed. Anything else (most importantly
    'production') raises ProductionEnvironmentError. This is the single choke point
    the whole script depends on — keep it pure (str in, raise or return) so
    --self-check can exercise it with no DB or network."""
    env = (environment or "").strip().lower()
    if env != "sandbox":
        raise ProductionEnvironmentError(
            f"QBO environment is '{environment}', not 'sandbox'. "
            "This smoke test refuses to touch a non-sandbox company. "
            "Point the ERP at a QuickBooks sandbox (Settings → QuickBooks → "
            "Environment = Sandbox, reconnect) before running."
        )


def _self_check() -> int:
    """Unit-check the gate with no DB/network. Returns a process exit code."""
    print("Self-check: environment safety gate")
    rep = Report()

    # sandbox → passes (no raise)
    try:
        assert_sandbox("sandbox")
        rep.add("sandbox environment is allowed", True)
    except ProductionEnvironmentError as exc:  # pragma: no cover - would be a bug
        rep.add("sandbox environment is allowed", False, str(exc))

    # case / whitespace tolerance
    try:
        assert_sandbox("  SANDBOX  ")
        rep.add("environment match is case/space tolerant", True)
    except ProductionEnvironmentError as exc:  # pragma: no cover
        rep.add("environment match is case/space tolerant", False, str(exc))

    # production → blocked
    for bad in ("production", "prod", "", "live", None):
        blocked = False
        try:
            assert_sandbox(bad)  # type: ignore[arg-type]
        except ProductionEnvironmentError:
            blocked = True
        rep.add(f"non-sandbox {bad!r} is blocked", blocked)

    print(f"\nSelf-check: {rep.passed} passed, {rep.failed} failed")
    return 0 if rep.failed == 0 else 1


# ── preconditions ────────────────────────────────────────────────────────────────

def _preflight(db, svc, client_cls) -> tuple[bool, dict]:
    """Run every guard the real push would hit, up front, so a misconfigured
    sandbox fails with actionable text BEFORE we create anything.

    Returns (ok, {"item_ids": {...}}). On failure prints why and returns ok=False.
    Order matters: the sandbox gate runs FIRST (before any read that could touch a
    production realm)."""
    from app.services.qbo_client import QBONotConnected

    rep = Report()

    # 1) connected?
    summary = svc.connection_summary()
    if not summary.get("connected"):
        rep.add("QBO is connected", False,
                "not connected — Settings → QuickBooks → Connect first")
        return False, {}
    rep.add("QBO is connected", True,
            f"realm {summary.get('realm_id', '?')}")

    # 2) THE SAFETY GATE — refuse anything but sandbox, before any further work.
    env = summary.get("environment", "")
    try:
        assert_sandbox(env)
    except ProductionEnvironmentError as exc:
        rep.add("environment is SANDBOX (safety gate)", False, str(exc))
        return False, {}
    rep.add("environment is SANDBOX (safety gate)", True, f"environment={env!r}")

    # 3) the client actually builds (token/realm usable)
    try:
        client = client_cls(db)
    except QBONotConnected as exc:
        rep.add("QBO client initialises", False, str(exc))
        return False, {}
    # Belt-and-suspenders: assert the client's own api_base is the sandbox host,
    # not just the stored setting (the summary reads the setting; this reads what
    # the client will actually call).
    if "sandbox" not in (client.cfg.api_base or "").lower():
        rep.add("QBO client targets the sandbox API host", False,
                f"api_base={client.cfg.api_base!r} is not the sandbox host")
        return False, {}
    rep.add("QBO client targets the sandbox API host", True, client.cfg.api_base)

    # 4) list_accounts() returns ok (chart of accounts readable)
    acc = svc.list_accounts()
    if not acc.get("ok"):
        rep.add("list_accounts() returns ok", False, acc.get("error", ""))
        return False, {}
    rep.add("list_accounts() returns ok", True,
            f"{len(acc.get('accounts', []))} accounts")

    # 5) the generic items exist — call the SAME check _resolve_items uses.
    try:
        item_ids = svc._resolve_items(client)
    except Exception as exc:  # QBOError with the "Set up QBO items" remediation
        rep.add("generic QBO items exist", False, str(exc))
        print("\n  REMEDIATION: open Settings → QuickBooks and click "
              "'Set up QBO items' (one time), then re-run.")
        return False, {}
    rep.add("generic QBO items exist", True,
            f"{len(item_ids)} items resolved")

    return rep.failed == 0, {"item_ids": item_ids}


# ── smoke fixtures (created through the ERP, not hand-inserted where a service
#    exists) ──────────────────────────────────────────────────────────────────────

def _get_or_create_smoke_customer(db, tag: str):
    from app.models.customer import Customer
    name = f"{SMOKE_PREFIX} Customer {tag}"
    cust = db.query(Customer).filter(Customer.company_name == name).first()
    if cust is None:
        cust = Customer(
            company_name=name,
            contact_name="Smoke Tester",
            email=f"smoke-{tag.lower()}@example.invalid",
            phone="555-0100",
            is_tax_exempt=True,           # keep tax out of the picture for clarity
            tax_rate=0.0,
            # O6 — force a known surcharge so the surcharge SalesReceipt path fires.
            card_surcharge_pct=3.0,
        )
        db.add(cust)
        db.commit()
        db.refresh(cust)
    return cust


def _get_or_create_smoke_vendor(db, tag: str):
    from app.models.vendor import Vendor
    name = f"{SMOKE_PREFIX} Vendor {tag}"
    vend = db.query(Vendor).filter(Vendor.name == name).first()
    if vend is None:
        vend = Vendor(name=name, email="smoke-vendor@example.invalid",
                      phone="555-0200")
        db.add(vend)
        db.commit()
        db.refresh(vend)
    return vend


def _get_or_create_smoke_product(db, tag: str, suffix: str, *, has_core: bool):
    """Throwaway product. has_core=True gives it a customer/vendor core charge so
    the invoice auto-adds a CORE_CHARGE child line (the real create_invoice path)."""
    from app.models.product import Product
    sku = f"ZZ-SMOKE-{tag}-{suffix}"
    prod = db.query(Product).filter(Product.sku == sku).first()
    if prod is None:
        prod = Product(
            sku=sku,
            title=f"{SMOKE_PREFIX} Part {suffix}",
            cost=40.0,
            qty_on_hand=1000,             # plenty so finalize never blocks on stock
            has_core=has_core,
            customer_core_charge=50.0 if has_core else 0.0,
            vendor_core_charge=35.0 if has_core else 0.0,
            is_active=True,
        )
        db.add(prod)
        db.commit()
        db.refresh(prod)
    return prod


# ── phase 1: invoice (product + core + freight) ──────────────────────────────────

def _phase_invoice(db, svc, client, item_ids, rep: Report, tag: str):
    """Create a finalized invoice with product + core + freight, push it, read it
    back, and assert the three account mappings. Returns the pushed Invoice or None."""
    from app.constants import InvoiceStatus, LineType, QBOSyncStatus
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService

    cust = _get_or_create_smoke_customer(db, tag)
    prod = _get_or_create_smoke_product(db, tag, "CORE", has_core=True)

    inv_svc = InvoiceService(db, current_user_id=None)
    # create_invoice auto-adds the CORE_CHARGE child for a core-bearing product,
    # so we only pass the PRODUCT line + an explicit FREIGHT line.
    invoice = inv_svc.create_invoice(
        customer_id=cust.id,
        data={"notes": f"{SMOKE_PREFIX} invoice {tag}", "is_taxable": False},
        lines=[
            {"line_type": LineType.PRODUCT, "product_id": prod.id,
             "description": f"{SMOKE_PREFIX} reman part", "qty": 1,
             "unit_price": 100.0, "unit_cost": 40.0, "is_taxable": False},
            {"line_type": LineType.FREIGHT,
             "description": f"{SMOKE_PREFIX} freight", "qty": 1,
             "unit_price": 25.0, "unit_cost": 0.0, "is_taxable": False},
        ],
    )
    inv_svc.finalise(invoice.id)

    line_types = {ln.line_type for ln in invoice.lines}
    rep.add("invoice has product + core + freight lines",
            {LineType.PRODUCT, LineType.CORE_CHARGE, LineType.FREIGHT} <= line_types,
            f"line types: {sorted(line_types)}")

    res = svc.push_invoice(invoice.id)
    if not res.get("ok") or res.get("skipped"):
        rep.add("invoice pushed to QBO", False,
                res.get("error") or f"skipped: {res.get('skipped')}")
        return None
    qbo_id = res["qbo_id"]
    db.refresh(invoice)
    rep.add("invoice pushed to QBO", True, f"QBO Invoice {qbo_id}")
    rep.add("invoice marked SYNCED locally",
            invoice.qbo_sync_status == QBOSyncStatus.SYNCED
            and invoice.qbo_invoice_id == qbo_id,
            f"status={invoice.qbo_sync_status}, id={invoice.qbo_invoice_id}")

    # Read the invoice back from QBO and assert per-line item mapping.
    _verify_sales_doc_lines(
        client, rep, entity="Invoice", qbo_id=qbo_id,
        expected={
            item_ids["JAKS Parts Sales"]: 100.0,
            item_ids["JAKS Core Charge"]: 50.0,
            item_ids["JAKS Freight & Delivery"]: 25.0,
        },
        label="invoice",
    )
    return invoice


def _verify_sales_doc_lines(client, rep, *, entity, qbo_id, expected, label):
    """Read a SalesItemLineDetail doc (Invoice / CreditMemo) back from QBO and
    assert each expected (item_id → amount) pair appears in its lines.

    expected maps QBO Item id → the line Amount we expect on that item."""
    from app.services.qbo_service import _q
    rows = client.query(f"select * from {entity} where Id = '{_q(str(qbo_id))}'")
    if not rows:
        rep.add(f"{label}: read back from QBO", False, "no row returned")
        return
    doc = rows[0]
    lines = [l for l in doc.get("Line", [])
             if l.get("DetailType") == "SalesItemLineDetail"]
    got: dict[str, float] = {}
    for l in lines:
        d = l.get("SalesItemLineDetail", {})
        item = str((d.get("ItemRef") or {}).get("value", ""))
        got[item] = round(float(l.get("Amount", 0) or 0), 2)
    for item_id, amount in expected.items():
        got_amt = got.get(str(item_id))
        ok = got_amt is not None and abs(got_amt - amount) < 0.01
        rep.add(f"{label}: item {item_id} posts ${amount:.2f}", ok,
                f"got {got_amt}")


# ── phase 2: payment + surcharge SalesReceipt ────────────────────────────────────

def _phase_payment(db, svc, client, rep: Report, tag: str, invoice):
    """Record a card payment (with surcharge) against the invoice, push it, and
    verify both the QBO Payment (LinkedTxn to the invoice) and the separate
    surcharge SalesReceipt."""
    from app.constants import PaymentMethod, QBOSyncStatus
    from app.models.invoice import Payment
    from app.services.payment_service import PaymentService

    if invoice is None:
        rep.add("payment: prerequisite invoice", False, "invoice phase did not produce a synced invoice")
        return None

    pay_svc = PaymentService(db, current_user_id=None)
    payment = pay_svc.record_payment(
        customer_id=invoice.customer_id,
        amount_received=invoice.balance_due,
        payment_method=PaymentMethod.CREDIT_CARD,
        data={"notes": f"{SMOKE_PREFIX} payment {tag}"},
        invoice_ids=[invoice.id],
        apply_surcharge=True,           # forces the surcharge SalesReceipt path
        surcharge_pct=3.0,
    )
    rep.add("payment recorded with a card surcharge",
            (payment.surcharge_amount or 0) > 0,
            f"surcharge ${payment.surcharge_amount:.2f}")

    res = svc.push_payment(payment.id)
    if not res.get("ok") or res.get("skipped"):
        rep.add("payment pushed to QBO", False,
                res.get("error") or f"skipped: {res.get('skipped')}")
        return None
    qbo_id = res["qbo_id"]
    db.refresh(payment)
    rep.add("payment pushed to QBO", True, f"QBO Payment {qbo_id}")
    rep.add("payment marked SYNCED locally",
            payment.qbo_sync_status == QBOSyncStatus.SYNCED
            and payment.qbo_payment_id == qbo_id,
            f"status={payment.qbo_sync_status}")

    # Verify the QBO Payment links back to the invoice.
    _verify_payment_link(client, rep, qbo_id=qbo_id,
                         invoice_qbo_id=invoice.qbo_invoice_id)

    # Verify the surcharge SalesReceipt exists (DocNumber JAKS-SC-{id}).
    sc_qbo_id = res.get("surcharge_qbo_id") or payment.qbo_surcharge_receipt_id
    rep.add("surcharge SalesReceipt created", bool(sc_qbo_id),
            f"SalesReceipt {sc_qbo_id}")
    if sc_qbo_id:
        _verify_surcharge_receipt(client, rep, sc_qbo_id=sc_qbo_id,
                                  payment_id=payment.id,
                                  expected_amount=payment.surcharge_amount)
    return payment


def _verify_payment_link(client, rep, *, qbo_id, invoice_qbo_id):
    from app.services.qbo_service import _q
    rows = client.query(f"select * from Payment where Id = '{_q(str(qbo_id))}'")
    if not rows:
        rep.add("payment: read back from QBO", False, "no row returned")
        return
    linked_ids: set[str] = set()
    for l in rows[0].get("Line", []):
        for lt in l.get("LinkedTxn", []):
            if lt.get("TxnType") == "Invoice":
                linked_ids.add(str(lt.get("TxnId")))
    ok = str(invoice_qbo_id) in linked_ids
    rep.add("payment links to the invoice in QBO", ok,
            f"LinkedTxn invoice ids: {sorted(linked_ids)}")


def _verify_surcharge_receipt(client, rep, *, sc_qbo_id, payment_id,
                              expected_amount):
    from app.services.qbo_service import SURCHARGE_ITEM, _q
    rows = client.query(f"select * from SalesReceipt where Id = '{_q(str(sc_qbo_id))}'")
    if not rows:
        rep.add("surcharge receipt: read back from QBO", False, "no row returned")
        return
    doc = rows[0]
    doc_number = doc.get("DocNumber", "")
    rep.add("surcharge receipt DocNumber is deterministic",
            doc_number == f"JAKS-SC-{payment_id}",
            f"DocNumber={doc_number!r}")
    # Amount check: the single line's Amount equals the surcharge.
    total = round(float(doc.get("TotalAmt", 0) or 0), 2)
    rep.add("surcharge receipt amount matches",
            abs(total - round(float(expected_amount), 2)) < 0.01,
            f"TotalAmt={total}, expected {expected_amount}")
    # Item check: it uses the JAKS Card Surcharge item.
    item_names = _resolve_item_names(client, doc)
    rep.add(f"surcharge receipt uses the '{SURCHARGE_ITEM}' item",
            SURCHARGE_ITEM in item_names, f"items: {sorted(item_names)}")


def _resolve_item_names(client, doc) -> set[str]:
    """Given a QBO sales doc dict, resolve its line items back to item NAMES."""
    from app.services.qbo_service import _q
    ids: set[str] = set()
    for l in doc.get("Line", []):
        if l.get("DetailType") == "SalesItemLineDetail":
            ids.add(str((l["SalesItemLineDetail"].get("ItemRef") or {}).get("value", "")))
    names: set[str] = set()
    for i in ids:
        if not i:
            continue
        r = client.query(f"select Id, Name from Item where Id = '{_q(i)}'")
        if r:
            names.add(r[0].get("Name", ""))
    return names


# ── phase 3: vendor bill (with freight) from a PO + receipt ───────────────────────

def _phase_vendor_bill(db, svc, client, rep: Report, tag: str, vendor):
    """Build a PO → send → receive → create a vendor bill (with freight) → approve,
    then push it and read the QBO Bill back to check the expense/freight lines."""
    from app.constants import QBOSyncStatus
    from app.models.purchase_order import PurchaseOrder, VendorBill
    from app.services.po_service import POService

    po_svc = POService(db, current_user_id=None)
    prod = _get_or_create_smoke_product(db, tag, "BILL", has_core=False)

    po = po_svc.create_po(vendor.id, {
        "notes": f"{SMOKE_PREFIX} PO {tag}",
        "freight_in_cost": 15.0,          # becomes the bill's freight line
    })
    line = po_svc.add_line(po.id, prod.id, {
        "qty_ordered": 2, "unit_cost": 40.0,
        "description": f"{SMOKE_PREFIX} bought part",
    })
    po_svc.send_to_vendor(po.id)
    po_svc.create_receipt(vendor.id, {line.id: 2},
                          {"notes": f"{SMOKE_PREFIX} receipt {tag}"})

    bill = po_svc.create_vendor_bill(
        po_id=po.id,
        vendor_id=vendor.id,
        bill_number=f"ZZSMK-{tag}-{_short_stamp()}",   # unique per run
        bill_date=datetime.utcnow(),
        due_date=datetime.utcnow() + timedelta(days=30),
        lines=[{"po_line_id": line.id, "qty_billed": 2, "unit_cost": 40.0}],
        # freight_amount=None → defaults to the PO's freight_in_cost (15.0).
    )
    # A clean 3-way match lands PENDING; approve it so it becomes pushable.
    po_svc.approve_bill(bill.id)
    db.refresh(bill)
    rep.add("vendor bill is APPROVED + has a freight amount",
            bill.status == "approved" and (bill.freight_amount or 0) > 0,
            f"status={bill.status}, freight=${bill.freight_amount:.2f}, "
            f"total=${bill.total_amount:.2f}")

    res = svc.push_vendor_bill(bill.id)
    if not res.get("ok") or res.get("skipped"):
        rep.add("vendor bill pushed to QBO", False,
                res.get("error") or f"skipped: {res.get('skipped')}")
        return None
    qbo_id = res["qbo_id"]
    db.refresh(bill)
    rep.add("vendor bill pushed to QBO", True, f"QBO Bill {qbo_id}")
    rep.add("vendor bill marked SYNCED locally",
            bill.qbo_sync_status == QBOSyncStatus.SYNCED and bill.qbo_id == qbo_id,
            f"status={bill.qbo_sync_status}")

    _verify_bill_lines(db, svc, client, rep, qbo_id=qbo_id,
                       parts_total=80.0, freight_total=15.0)
    return bill


def _verify_bill_lines(db, svc, client, rep, *, qbo_id, parts_total, freight_total):
    """Read the QBO Bill back and assert: (a) the expense line posts to the
    configured COGS/expense account, (b) a freight-in line exists. We resolve the
    expected account ids through the SAME resolvers the push used so this test
    honours whatever the owner mapped in Settings → QBO Accounts."""
    from app.services.qbo_service import _q
    rows = client.query(f"select * from Bill where Id = '{_q(str(qbo_id))}'")
    if not rows:
        rep.add("vendor bill: read back from QBO", False, "no row returned")
        return
    doc = rows[0]
    exp_account_id = svc._resolve_bill_expense_account(client)
    freight_account_id = svc._resolve_freight_in_account(client, exp_account_id)

    by_account: dict[str, float] = {}
    for l in doc.get("Line", []):
        if l.get("DetailType") != "AccountBasedExpenseLineDetail":
            continue
        acct = str((l["AccountBasedExpenseLineDetail"].get("AccountRef") or {}).get("value", ""))
        by_account[acct] = round(by_account.get(acct, 0.0)
                                 + float(l.get("Amount", 0) or 0), 2)

    parts_ok = abs(by_account.get(exp_account_id, 0.0) - parts_total) < 0.01
    rep.add("vendor bill parts line posts to the expense/COGS account", parts_ok,
            f"account {exp_account_id} got ${by_account.get(exp_account_id, 0.0):.2f}, "
            f"expected ${parts_total:.2f}")

    # Freight may share the expense account (fallback) or a dedicated freight-in
    # account; assert the total across whichever account carries it.
    if freight_account_id == exp_account_id:
        # freight folded into the same account → total should be parts+freight
        combined = by_account.get(exp_account_id, 0.0)
        rep.add("vendor bill freight posts (folded into expense account)",
                abs(combined - (parts_total + freight_total)) < 0.01,
                f"combined ${combined:.2f}, expected ${parts_total + freight_total:.2f}")
    else:
        freight_ok = abs(by_account.get(freight_account_id, 0.0) - freight_total) < 0.01
        rep.add("vendor bill freight posts to the freight-in account", freight_ok,
                f"account {freight_account_id} got "
                f"${by_account.get(freight_account_id, 0.0):.2f}, "
                f"expected ${freight_total:.2f}")


# ── phase 4: credit memo (core line mapped) ──────────────────────────────────────

def _phase_credit_memo(db, svc, client, item_ids, rep: Report, tag: str, invoice):
    """Issue a credit memo against the invoice — one line back-linked to the
    invoice's CORE_CHARGE line (so it maps to JAKS Core Charge) and one plain
    parts line — push it, and read it back to check the item mapping."""
    from app.constants import CreditMemoTrigger, LineType, QBOSyncStatus
    from app.models.credit_memo import CreditMemo
    from app.services.credit_memo_service import CreditMemoService

    if invoice is None:
        rep.add("credit memo: prerequisite invoice", False, "invoice phase did not produce a synced invoice")
        return None

    core_line = next((ln for ln in invoice.lines
                      if ln.line_type == LineType.CORE_CHARGE), None)

    cm_svc = CreditMemoService(db, current_user_id=None)
    cm = cm_svc.create_credit_memo(
        customer_id=invoice.customer_id,
        trigger_type=CreditMemoTrigger.MANUAL,
        original_invoice_id=invoice.id,
        reason=f"{SMOKE_PREFIX} credit {tag}",
        lines=[
            # Back-linked to the core line → must map to JAKS Core Charge.
            {"description": f"{SMOKE_PREFIX} core returned", "qty": 1,
             "unit_price": 50.0,
             "original_invoice_line_id": core_line.id if core_line else None,
             "is_taxable": False},
            # Plain parts credit → maps to JAKS Parts Sales.
            {"description": f"{SMOKE_PREFIX} parts credit", "qty": 1,
             "unit_price": 30.0, "is_taxable": False},
        ],
    )

    res = svc.push_credit_memo(cm.id)
    if not res.get("ok") or res.get("skipped"):
        rep.add("credit memo pushed to QBO", False,
                res.get("error") or f"skipped: {res.get('skipped')}")
        return None
    qbo_id = res["qbo_id"]
    db.refresh(cm)
    rep.add("credit memo pushed to QBO", True, f"QBO CreditMemo {qbo_id}")
    rep.add("credit memo marked SYNCED locally",
            cm.qbo_sync_status == QBOSyncStatus.SYNCED and cm.qbo_id == qbo_id,
            f"status={cm.qbo_sync_status}")

    _verify_sales_doc_lines(
        client, rep, entity="CreditMemo", qbo_id=qbo_id,
        expected={
            item_ids["JAKS Core Charge"]: 50.0,
            item_ids["JAKS Parts Sales"]: 30.0,
        },
        label="credit memo",
    )
    return cm


# ── cleanup ──────────────────────────────────────────────────────────────────────

def _cleanup(db, svc, client, tag: str) -> int:
    """Best-effort teardown for a run tag:
      * void/delete the QBO docs where the API allows (Invoice/Payment/CreditMemo/
        SalesReceipt support delete; Bill supports delete);
      * always delete the local ZZ SMOKE TEST records.
    Prints what could not be auto-deleted so the owner can finish in the sandbox."""
    print(f"Cleanup for run tag {tag!r}:")

    # --- local records ---
    from app.models.credit_memo import CreditMemo, CreditMemoAllocation
    from app.models.customer import Customer
    from app.models.invoice import Invoice, Payment, PaymentAllocation
    from app.models.product import Product
    from app.models.purchase_order import (
        POReceipt, POReceiptLine, PurchaseOrder, VendorBill,
    )
    from app.models.vendor import Vendor

    cust = db.query(Customer).filter(
        Customer.company_name == f"{SMOKE_PREFIX} Customer {tag}").first()
    vend = db.query(Vendor).filter(
        Vendor.name == f"{SMOKE_PREFIX} Vendor {tag}").first()

    # Try to void QBO documents first (so the sandbox isn't left with orphans).
    _qbo_delete_docs_for_customer(client, cust)

    # Delete local rows in FK-safe order. SQLite FKs are ON, so children first.
    deleted = {"invoices": 0, "payments": 0, "credit_memos": 0,
               "vendor_bills": 0, "pos": 0, "products": 0}
    if cust is not None:
        for cm in db.query(CreditMemo).filter(CreditMemo.customer_id == cust.id).all():
            db.query(CreditMemoAllocation).filter(
                CreditMemoAllocation.credit_memo_id == cm.id).delete()
            db.delete(cm); deleted["credit_memos"] += 1
        for pmt in db.query(Payment).filter(Payment.customer_id == cust.id).all():
            db.query(PaymentAllocation).filter(
                PaymentAllocation.payment_id == pmt.id).delete()
            db.delete(pmt); deleted["payments"] += 1
        for inv in db.query(Invoice).filter(Invoice.customer_id == cust.id).all():
            db.delete(inv); deleted["invoices"] += 1
    if vend is not None:
        for bill in db.query(VendorBill).filter(VendorBill.vendor_id == vend.id).all():
            db.delete(bill); deleted["vendor_bills"] += 1
        # receipts reference po_lines; delete receipts then POs (cascade lines).
        for po in db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vend.id).all():
            for rl in db.query(POReceiptLine).filter(POReceiptLine.po_id == po.id).all():
                rcpt = db.query(POReceipt).filter(POReceipt.id == rl.receipt_id).first()
                db.query(POReceiptLine).filter(
                    POReceiptLine.receipt_id == rl.receipt_id).delete()
                if rcpt is not None:
                    db.delete(rcpt)
            db.delete(po); deleted["pos"] += 1
    db.commit()

    for prod in db.query(Product).filter(Product.sku.like(f"ZZ-SMOKE-{tag}-%")).all():
        db.delete(prod); deleted["products"] += 1
    if cust is not None:
        db.delete(cust)
    if vend is not None:
        db.delete(vend)
    db.commit()

    print(f"  local records deleted: {deleted}")
    print("  NOTE: any QBO documents that could not be auto-deleted (some sandbox "
          "configs restrict delete) can be voided by hand — search the sandbox for "
          f"'{SMOKE_PREFIX}'.")
    return 0


def _qbo_delete_docs_for_customer(client, cust) -> None:
    """Void/delete this customer's QBO Invoices, Payments, CreditMemos, and the
    surcharge SalesReceipts. Delete via the QBO 'operation=delete' POST; each is
    best-effort so one failure never aborts the rest."""
    if client is None or cust is None or not cust.qbo_customer_id:
        return
    from app.services.qbo_service import _q
    cust_ref = _q(str(cust.qbo_customer_id))
    for entity in ("Invoice", "Payment", "CreditMemo", "SalesReceipt"):
        try:
            rows = client.query(
                f"select Id, SyncToken from {entity} "
                f"where CustomerRef = '{cust_ref}'")
        except Exception as exc:
            print(f"  (could not query {entity} for cleanup: {exc})")
            continue
        for r in rows:
            rid, token = r.get("Id"), str(r.get("SyncToken", "0"))
            if not rid:
                continue
            try:
                client.request("POST", entity.lower(),
                               params={"operation": "delete"},
                               json={"Id": str(rid), "SyncToken": token})
                print(f"  deleted QBO {entity} {rid}")
            except Exception as exc:
                print(f"  (could not delete QBO {entity} {rid}: {exc})")


# ── misc helpers ─────────────────────────────────────────────────────────────────

def _short_stamp() -> str:
    """A short, sortable stamp so re-runs never collide on a vendor bill number."""
    return datetime.utcnow().strftime("%m%d%H%M%S")


# ── main ─────────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="qbo_sandbox_smoke",
        description="Push one of each document type to a QBO SANDBOX and verify "
                    "the account mapping. Refuses to run against production.")
    parser.add_argument("--db", help="alternate SQLite DB path (sets JAKS_DB_PATH)")
    parser.add_argument("--run-tag", default="SMOKE",
                        help="marker suffix for this run's records (default SMOKE)")
    parser.add_argument("--check-only", action="store_true",
                        help="run preconditions checks and exit (no writes/pushes)")
    parser.add_argument("--self-check", action="store_true",
                        help="unit-check the sandbox gate only; no DB/network")
    parser.add_argument("--cleanup", action="store_true",
                        help="void/delete this run's smoke docs, then exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    # --self-check runs BEFORE any DB import — it must touch nothing.
    if args.self_check:
        return _self_check()

    # --db must be set before app.database is imported (it reads JAKS_DB_PATH once).
    if args.db:
        os.environ["JAKS_DB_PATH"] = args.db

    from app.database import SessionLocal
    from app.services.qbo_client import QBOClient, QBONotConnected
    from app.services.qbo_service import QBOSyncService

    tag = args.run_tag.strip() or "SMOKE"
    db = SessionLocal()
    try:
        svc = QBOSyncService(db, current_user_id=None)

        # Every path first proves we are connected to a SANDBOX.
        ok, ctx = _preflight(db, svc, QBOClient)
        if not ok:
            print("\nPreconditions failed — not proceeding.")
            return 2

        if args.check_only:
            print("\nPreconditions OK (--check-only): safe to run the full smoke test.")
            return 0

        try:
            client = QBOClient(db)
        except QBONotConnected as exc:
            print(f"QBO client init failed: {exc}")
            return 2

        if args.cleanup:
            return _cleanup(db, svc, client, tag)

        item_ids = ctx["item_ids"]
        rep = Report()

        print(f"\n=== QBO SANDBOX smoke test (run tag: {tag}) ===")
        vendor = _get_or_create_smoke_vendor(db, tag)

        print("\n--- Phase 1: Invoice (product + core + freight) ---")
        invoice = _phase_invoice(db, svc, client, item_ids, rep, tag)

        print("\n--- Phase 2: Payment + card-surcharge SalesReceipt ---")
        _phase_payment(db, svc, client, rep, tag, invoice)

        print("\n--- Phase 3: Vendor bill (with freight) ---")
        _phase_vendor_bill(db, svc, client, rep, tag, vendor)

        print("\n--- Phase 4: Credit memo (core line mapped) ---")
        _phase_credit_memo(db, svc, client, item_ids, rep, tag, invoice)

        print("\n=== SUMMARY ===")
        print(f"  checks passed: {rep.passed}")
        print(f"  checks failed: {rep.failed}")
        if rep.failed:
            print("\n  FAILED CHECKS:")
            for c in rep.checks:
                if not c.ok:
                    print(f"    - {c.name}: {c.detail}")
            print(f"\n  Re-run '--cleanup --run-tag {tag}' to remove the smoke "
                  "docs, fix the mapping, and try again.")
            return 1

        print(f"\n  ALL CHECKS PASSED. Run '--cleanup --run-tag {tag}' to remove "
              "the ZZ SMOKE TEST documents from the sandbox + local DB.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
