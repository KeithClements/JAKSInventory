"""
QBO history importer  (scripts/qbo_history_import.py)
=====================================================
Loads the production QBO A/R + open A/P history — pulled read-only to
``data/qbo_staging/*.json`` via the Claude QBO MCP — into a JAKS ERP database so
the 30-day sandbox trial operates on realistic data.

SAFETY
------
* Runs ONLY against the DB named by ``--db`` (default ``data/jaks_sandbox.db``),
  pointed at via ``JAKS_DB_PATH`` before ``app.database`` is imported.
* Refuses to run unless that DB's stored QBO connection is the SANDBOX realm
  (never the production realm 9341455493585007).
* Every imported row is tagged ``QBO_HISTORY_IMPORT`` and stamped
  ``qbo_sync_status = SKIPPED`` so it is never auto-pushed to QBO. Push a doc
  deliberately only if you want it mirrored into the sandbox.
* Dry-run by default (builds everything in a transaction, prints the
  reconciliation, then ROLLS BACK). Pass ``--apply`` to commit.

Phases: customers -> product crosswalk -> invoices (+covering payments) -> open
bills -> reconcile. Idempotent: re-running skips docs already imported.

    # sandbox (default target, unchanged):
    .venv\\Scripts\\python.exe -m scripts.qbo_history_import                 # dry-run
    .venv\\Scripts\\python.exe -m scripts.qbo_history_import --apply          # commit

    # LIVE operational DB (go-live load) — dry-run needs no confirmation:
    .venv\\Scripts\\python.exe -m scripts.qbo_history_import --db data/jaks.db --live --since 2025-07-07
    # ...and to actually commit to live, the exact confirm phrase is required:
    .venv\\Scripts\\python.exe -m scripts.qbo_history_import --db data/jaks.db --live --since 2025-07-07 \\
        --apply --confirm "LOAD QBO HISTORY INTO LIVE"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

TAG = "QBO_HISTORY_IMPORT"
PROD_REALM = "9341455493585007"
INVOICE_PREFIX = "QBO-"
JUNK_CUSTOMERS = {"sample customer"}
LIVE_CONFIRM_PHRASE = "LOAD QBO HISTORY INTO LIVE"

# Reconciliation targets — refreshed 2026-07-07 from the production book
# (JAK's Diesel, LLC). Balances move daily; re-pull staging + refresh these
# right before a live apply. Used only for the PASS/FAIL readout, not a gate.
AR_TARGET = 70383.74
GRAND_TARGET = 478000.62
OPEN_TARGET = 20
AP_TARGET = 62970.34


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Import QBO history into a JAKS test DB")
    ap.add_argument("--db", default="data/jaks_sandbox.db")
    ap.add_argument("--staging", default="data/qbo_staging")
    ap.add_argument("--apply", action="store_true", help="commit (default is dry-run)")
    ap.add_argument("--purge", action="store_true", help="delete prior QBO_HISTORY_IMPORT rows first")
    ap.add_argument("--live", action="store_true",
                    help="target the LIVE operational DB (bypasses the sandbox-realm refusal); "
                         "a live --apply additionally requires --confirm")
    ap.add_argument("--confirm", default="",
                    help=f"exact phrase required for a live apply: {LIVE_CONFIRM_PHRASE!r}")
    ap.add_argument("--since", default="",
                    help="YYYY-MM-DD floor for CLOSED (paid) invoices; OPEN/PARTIAL are always "
                         "imported regardless of age. Default: import everything staged.")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    db_abs = os.path.abspath(args.db)
    if not os.path.exists(db_abs):
        print(f"FATAL: db not found: {db_abs}")
        return 2
    os.environ["JAKS_DB_PATH"] = db_abs  # MUST precede app.database import

    from app.database import SessionLocal
    from app.utils import normalize_part
    from app.constants import (
        InvoiceStatus, LineType, QBOSyncStatus, PaymentMethod, PaymentStatus,
        PaymentDirection, InvoiceLockReason, VendorBillStatus,
    )
    from app.models.customer import Customer
    from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
    from app.models.product import Product, CrossReference
    from app.models.vendor import Vendor
    from app.models.purchase_order import VendorBill
    from sqlalchemy import text as _sql

    db = SessionLocal()

    # -- SAFETY GATE: sandbox-only --------------------------------------------
    settings = dict(db.execute(_sql(
        "SELECT key, value FROM settings WHERE key IN ('qbo_environment','qbo_realm_id')"
    )).all())
    env = (settings.get("qbo_environment") or "").strip().lower()
    realm = (settings.get("qbo_realm_id") or "").strip()
    print(f"Target DB : {db_abs}")
    print(f"QBO conn  : environment={env!r} realm={realm!r}")
    if args.live:
        print("Mode      : LIVE — targeting the live operational database.")
        if args.apply and args.confirm != LIVE_CONFIRM_PHRASE:
            print(f"REFUSING: a live --apply requires --confirm {LIVE_CONFIRM_PHRASE!r} "
                  f"(a dry-run needs no confirmation). Aborting.")
            db.close()
            return 2
    else:
        if env != "sandbox" or realm == PROD_REALM:
            print("REFUSING: target DB is not connected to the QBO SANDBOX. "
                  "Pass --live to load into the live operational DB. Aborting.")
            db.close()
            return 2
    print(f"Run       : {'APPLY (will commit)' if args.apply else 'DRY-RUN (rollback)'}"
          f"{'  +PURGE' if args.purge else ''}")

    since = _date(args.since) if args.since else None
    if since:
        print(f"Closed floor : {since.date()}  (--since; open/partial always imported)")
    print()

    # -- Optional purge of a prior import -------------------------------------
    if args.purge:
        pays = db.query(Payment).filter(Payment.notes == TAG).all()
        for p in pays:
            db.delete(p)                       # cascades allocations
        invs = db.query(Invoice).filter(Invoice.internal_notes == TAG).all()
        for inv in invs:
            db.delete(inv)                     # cascades lines
        bills = db.query(VendorBill).filter(VendorBill.notes == TAG).all()
        for b in bills:
            db.delete(b)
        db.flush()
        print(f"PURGE: removed {len(pays)} payments, {len(invs)} invoices, {len(bills)} bills (prior import)\n")

    # -- Load staging ---------------------------------------------------------
    with open(os.path.join(args.staging, "invoices_raw.json"), encoding="utf-8") as fh:
        invoices_raw = json.load(fh)
    with open(os.path.join(args.staging, "open_bills_raw.json"), encoding="utf-8") as fh:
        bills_doc = json.load(fh)
    bills_raw = bills_doc["bills"]

    # -- Customer resolver (match existing ERP customers; create if new) ------
    existing = db.query(Customer).all()
    by_name = {}
    emails = set()
    for c in existing:
        by_name.setdefault(normalize_part(c.company_name), c)
        if (c.contact_name or "").strip():
            by_name.setdefault(normalize_part(c.contact_name), c)
        if (c.email or "").strip():
            emails.add(c.email.strip().lower())

    cust_cache: dict[str, Customer] = {}
    created_customers: list[str] = []
    matched_customers: list[str] = []

    def resolve_customer(display_name: str, email: str):
        key = (display_name or "").strip()
        if not key or key.lower() in JUNK_CUSTOMERS:
            return None
        if key in cust_cache:
            return cust_cache[key]
        nk = normalize_part(key)
        c = by_name.get(nk)
        if c is None and (email or "").strip():
            for e in existing:
                if (e.email or "").strip().lower() == email.strip().lower():
                    c = e
                    break
        if c is None:
            safe_email = email.strip() if (email or "").strip() and email.strip().lower() not in emails else ""
            if safe_email:
                emails.add(safe_email.lower())
            c = Customer(company_name=key, email=safe_email, internal_notes=TAG)
            db.add(c)
            db.flush()
            by_name.setdefault(nk, c)
            created_customers.append(key)
        else:
            matched_customers.append(key)
        cust_cache[key] = c
        return c

    # -- Product crosswalk (QBO item -> ERP product via OEM cross-ref / SKU) --
    xwalk_cache: dict[str, tuple] = {}

    def _tokens(item_name: str):
        toks = []
        for raw in re.split(r"[^A-Za-z0-9]+", item_name or ""):
            n = normalize_part(raw)
            if len(n) >= 4 and any(ch.isdigit() for ch in n):
                toks.append(n)
        # de-dupe, keep order
        seen = set()
        return [t for t in toks if not (t in seen or seen.add(t))]

    def match_product(item_id: str, item_name: str):
        """Return (product_id, method, candidate_skus). Only auto-attach when a
        number resolves to EXACTLY ONE product — a shared OEM (e.g. 2239250 on
        five stage heads) is left for owner review with its candidate SKUs."""
        if item_id in xwalk_cache:
            return xwalk_cache[item_id]
        pid, method, cands = None, "", []
        toks = _tokens(item_name)
        # 1) SKU is globally unique — a single sku_norm hit is definitive.
        for tok in toks:
            rows = db.execute(_sql(
                "SELECT id, sku FROM products WHERE sku_norm = :t LIMIT 2"
            ), {"t": tok}).all()
            if len(rows) == 1:
                pid, method = rows[0][0], f"sku:{tok}"
                break
        # 2) OEM cross-ref — only trust an unambiguous (single-product) hit.
        if pid is None:
            for tok in toks:
                prods = db.execute(_sql(
                    "SELECT DISTINCT cr.product_id, p.sku FROM cross_references cr "
                    "JOIN products p ON p.id = cr.product_id "
                    "WHERE cr.ref_number_norm = :t LIMIT 6"
                ), {"t": tok}).all()
                if len(prods) == 1:
                    pid, method = prods[0][0], f"oem:{tok}"
                    break
                if len(prods) > 1 and not cands:
                    method = f"ambiguous:{tok}"
                    cands = [p[1] for p in prods]
        xwalk_cache[item_id] = (pid, method, cands)
        return pid, method, cands

    def line_type_for(item_name: str, description: str, item_id: str) -> str:
        n = (item_name or "").lower()
        d = (description or "").lower()
        if item_id == "SHIPPING_ITEM_ID" or "freight" in n or "shipping" in n or "shipping" in d:
            return LineType.FREIGHT
        if "core charge" in n or "core deposit" in n or n.startswith("cores:"):
            return LineType.CORE_CHARGE
        if "fuel surcharge" in n:
            return LineType.FUEL_SERVICE_CHARGE
        if "credit card fee" in n or "credit card fee" in d:
            return LineType.MISC_FEE
        return LineType.PRODUCT

    # -- Build invoices -------------------------------------------------------
    existing_numbers = {r[0] for r in db.query(Invoice.invoice_number).all()}
    created_inv = skipped_inv = filtered_inv = 0
    item_meta: dict[str, dict] = {}   # for crosswalk CSV

    for inv in invoices_raw:
        ref = str(inv.get("reference_number") or "").strip()
        display = (inv.get("contact") or {}).get("display_name", "")
        if not ref or (display or "").strip().lower() in JUNK_CUSTOMERS:
            continue
        number = f"{INVOICE_PREFIX}{ref}"
        if number in existing_numbers:
            skipped_inv += 1
            continue
        cust = resolve_customer(display, (inv.get("contact") or {}).get("email", ""))
        if cust is None:
            continue

        amount = round(_f(inv.get("amount")), 2)
        balance = round(_f(inv.get("balance_amount")), 2)
        txn = _date(inv.get("txn_date")) or datetime.utcnow()
        due = _date(inv.get("due_date"))
        paid = round(amount - balance, 2)
        if balance <= 0.005:
            status = InvoiceStatus.PAID
        elif paid > 0.005:
            status = InvoiceStatus.PARTIAL
        else:
            status = InvoiceStatus.OPEN

        # --since keeps all open A/R (any age) but floors CLOSED history.
        if since and status == InvoiceStatus.PAID and txn < since:
            filtered_inv += 1
            continue

        invoice = Invoice(
            invoice_number=number, customer_id=cust.id, status=status,
            discount_pct=0.0, apply_cc_surcharge=False, cc_surcharge_pct=0.0,
            is_taxable=False, tax_rate=0.0, tax_rate_snapshot=0.0, tax_exempt_snapshot=False,
            due_date=due, created_at=txn,
            locked_at=txn, lock_reason=InvoiceLockReason.MANUAL,
            qbo_sync_status=QBOSyncStatus.SKIPPED,
            notes=f"Imported from QBO invoice #{ref}", internal_notes=TAG,
        )
        line_sum = 0.0
        order = 0
        for ln in inv.get("lines", []):
            if "amount" not in ln:   # trailing {"subtotal": N} marker
                continue
            item_id = str(ln.get("item_id") or "")
            item_name = ln.get("item_name") or ""
            desc = ln.get("description") or item_name or "(no description)"
            amt = round(_f(ln.get("amount")), 2)
            rate = _f(ln.get("rate"))
            qraw = _f(ln.get("quantity"))
            if qraw > 0 and float(qraw).is_integer() and abs(qraw * rate - amt) < 0.01:
                qty, unit_price = int(qraw), rate
            else:
                qty, unit_price = 1, amt
            lt = line_type_for(item_name, desc, item_id)
            pid, method, cands = (None, "", [])
            if lt == LineType.PRODUCT and item_id and item_id != "SHIPPING_ITEM_ID":
                pid, method, cands = match_product(item_id, item_name)
            invoice.lines.append(InvoiceLine(
                product_id=pid, line_type=lt, description=desc[:500],
                qty=qty, unit_price=unit_price, unit_cost=0.0,
                is_taxable=False, sort_order=order,
            ))
            order += 1
            line_sum = round(line_sum + qty * unit_price, 2)
            if item_id:
                m = item_meta.setdefault(item_id, {
                    "item_name": item_name, "count": 0, "total": 0.0,
                    "product_id": pid, "method": method, "cands": cands,
                })
                m["count"] += 1
                m["total"] = round(m["total"] + amt, 2)

        # Force the line total to equal QBO's invoice total to the penny.
        drift = round(amount - line_sum, 2)
        if abs(drift) >= 0.01:
            invoice.lines.append(InvoiceLine(
                line_type=LineType.MISC_FEE, description="QBO total reconciliation adjustment",
                qty=1, unit_price=drift, unit_cost=0.0, is_taxable=False, sort_order=order,
            ))
        db.add(invoice)
        db.flush()

        if paid > 0.005:
            pay = Payment(
                customer_id=cust.id, amount_received=paid,
                payment_method=PaymentMethod.OTHER, payment_date=txn,
                direction=PaymentDirection.INCOMING_FROM_CUSTOMER,
                status=PaymentStatus.APPLIED, qbo_sync_status=QBOSyncStatus.SKIPPED,
                notes=TAG,
            )
            pay.allocations.append(PaymentAllocation(invoice_id=invoice.id, amount_applied=paid))
            db.add(pay)
            db.flush()
        existing_numbers.add(number)
        created_inv += 1

    # -- Customer-level unapplied credits / overpayments ----------------------
    # QBO reports A/R net of standalone unapplied credits (e.g. a $0.04 floating
    # overpayment). Apply each staged credit to the customer's OLDEST open
    # imported invoice so the ERP A/R matches QBO's reported A/R to the penny.
    credits_path = os.path.join(args.staging, "customer_credits_raw.json")
    applied_credits = 0
    credit_total = 0.0
    if os.path.exists(credits_path):
        with open(credits_path, encoding="utf-8") as fh:
            credits_raw = json.load(fh).get("credits", [])
        for cr in credits_raw:
            amt = round(_f(cr.get("amount")), 2)
            cust = resolve_customer(cr.get("customer", ""), "")
            if amt <= 0 or cust is None:
                continue
            target = (db.query(Invoice)
                      .filter(Invoice.customer_id == cust.id,
                              Invoice.internal_notes == TAG,
                              Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]))
                      .order_by(Invoice.created_at.asc(), Invoice.id.asc())
                      .first())
            if target is None:
                continue  # no open invoice to net against — leave for manual credit
            pay = Payment(
                customer_id=cust.id, amount_received=amt,
                payment_method=PaymentMethod.OTHER,
                payment_date=_date(cr.get("date")) or datetime.utcnow(),
                direction=PaymentDirection.INCOMING_FROM_CUSTOMER,
                status=PaymentStatus.APPLIED, qbo_sync_status=QBOSyncStatus.SKIPPED,
                notes=TAG,
            )
            pay.allocations.append(PaymentAllocation(invoice_id=target.id, amount_applied=amt))
            db.add(pay)
            db.flush()
            applied_credits += 1
            credit_total = round(credit_total + amt, 2)

    # -- Build open vendor bills ----------------------------------------------
    active_vendors = {normalize_part(v.name): v for v in db.query(Vendor).filter(Vendor.is_active == True).all()}  # noqa: E712
    # QBO bills these vendors under longer legal names that don't string-match the
    # configured ERP vendor names — alias them so we reuse the existing vendor
    # rows instead of creating duplicates. (Sampa USA LLC is genuinely new.)
    _VENDOR_ALIASES = {
        "Diesel Fuel Technologies LLC": "Diesel Fuel Technologies",
        "Migao- Michelle Wirrick": "Migao",
    }
    for _qbo_name, _erp_name in _VENDOR_ALIASES.items():
        _ev = active_vendors.get(normalize_part(_erp_name))
        if _ev is not None:
            active_vendors[normalize_part(_qbo_name)] = _ev
    created_bills = skipped_bills = 0
    created_vendors: list[str] = []
    for i, b in enumerate(bills_raw, 1):
        vname = b["vendor"].strip()
        vk = normalize_part(vname)
        vendor = active_vendors.get(vk)
        if vendor is None:
            vendor = Vendor(name=vname, is_active=True, internal_notes=TAG)
            db.add(vendor)
            db.flush()
            active_vendors[vk] = vendor
            created_vendors.append(vname)
        bill_number = (b.get("bill_number") or "").strip() or f"QBOAP-{i}"
        dup = db.query(VendorBill).filter(
            VendorBill.vendor_id == vendor.id, VendorBill.bill_number == bill_number
        ).first()
        if dup:
            skipped_bills += 1
            continue
        db.add(VendorBill(
            vendor_id=vendor.id, po_id=None, bill_number=bill_number,
            bill_date=_date(b.get("bill_date")), due_date=_date(b.get("due_date")),
            status=VendorBillStatus.PENDING, total_amount=round(_f(b["open_balance"]), 2),
            freight_amount=0.0, qbo_sync_status=QBOSyncStatus.SKIPPED,
            notes=TAG,
        ))
        created_bills += 1
    db.flush()

    # -- Reconcile ------------------------------------------------------------
    imported = db.query(Invoice).filter(Invoice.internal_notes == TAG).all()
    ar_total = round(sum(i.balance_due for i in imported), 2)
    grand_total = round(sum(i.total for i in imported), 2)
    open_ct = sum(1 for i in imported if i.balance_due > 0.005)
    ap_total = round(sum(
        b.total_amount for b in db.query(VendorBill).filter(VendorBill.notes == TAG).all()
    ), 2)
    matched_items = sum(1 for m in item_meta.values() if m["product_id"])

    # -- Crosswalk CSV for owner review ---------------------------------------
    import csv
    xpath = os.path.join(args.staging, "crosswalk_review.csv")
    with open(xpath, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["qbo_item_id", "qbo_item_name", "invoice_lines", "total_sold",
                    "matched_erp_product_id", "matched_erp_sku", "match_method",
                    "candidate_skus", "needs_review"])
        for iid, m in sorted(item_meta.items(), key=lambda kv: -kv[1]["total"]):
            sku = ""
            if m["product_id"]:
                r = db.execute(_sql("SELECT sku FROM products WHERE id = :i"), {"i": m["product_id"]}).first()
                sku = r[0] if r else ""
            w.writerow([iid, m["item_name"], m["count"], f'{m["total"]:.2f}',
                        m["product_id"] or "", sku, m["method"],
                        "; ".join(m.get("cands") or []),
                        "" if m["product_id"] else "YES"])

    def _chk(actual, target, tol=0.01):
        return "PASS" if abs(actual - target) <= tol else "**FAIL**"

    print("-- CUSTOMERS ------------------------------------------")
    print(f"  matched existing : {len(set(matched_customers))}")
    print(f"  created new      : {len(created_customers)}  {created_customers}")
    print("-- INVOICES -------------------------------------------")
    print(f"  created          : {created_inv}")
    print(f"  skipped (exists) : {skipped_inv}")
    print(f"  filtered (--since): {filtered_inv}")
    print(f"  account credits  : {applied_credits} applied  (${credit_total:,.2f})")
    print(f"  A/R balance_due  : ${ar_total:,.2f}   target ${AR_TARGET:,.2f}   [{_chk(ar_total, AR_TARGET)}]")
    print(f"  grand total      : ${grand_total:,.2f}   target ${GRAND_TARGET:,.2f}   [{_chk(grand_total, GRAND_TARGET)}]")
    print(f"  open invoices    : {open_ct}   target {OPEN_TARGET}   [{'PASS' if open_ct == OPEN_TARGET else '**FAIL**'}]")
    print("-- PRODUCT CROSSWALK ----------------------------------")
    print(f"  distinct items   : {len(item_meta)}")
    print(f"  auto-matched     : {matched_items}")
    print(f"  need review      : {len(item_meta) - matched_items}  -> {xpath}")
    print("-- OPEN VENDOR BILLS ----------------------------------")
    print(f"  created          : {created_bills}   skipped: {skipped_bills}")
    print(f"  created vendors  : {len(created_vendors)}  {created_vendors}")
    print(f"  A/P total        : ${ap_total:,.2f}   target ${AP_TARGET:,.2f}   [{_chk(ap_total, AP_TARGET)}]")
    print("  (targets refreshed 2026-07-07; balances move daily — re-pull before a live apply)")
    print("-------------------------------------------------------")

    if args.apply:
        db.commit()
        print("COMMITTED.")
    else:
        db.rollback()
        print("DRY-RUN complete — rolled back, no changes written.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
