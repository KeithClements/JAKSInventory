"""
app/services/qbo_service.py
===========================
QBO sync engine (Phase 1B) — pushes JAKS documents INTO QuickBooks Online.

Direction is one-way (JAKS → QBO); JAKS stays the operational source of truth,
QBO is the accounting book of record. Mapping strategy = **accounting summary**
(owner-locked 2026-06-02): every invoice line rolls up to one of a handful of
generic QBO *income items*, NOT per-SKU items — so QBO never runs a parallel
stockroom. The SKU + description still ride along in each line's Description so
detail prints on the QBO invoice.

CONTRACT: push_* methods are best-effort and **never raise**. Success →
InvoiceService.mark_synced (which also locks the invoice). Failure →
InvoiceService.mark_sync_failed (records the error, bumps retry count). A QBO
outage therefore can never block finalize, payment, or any money route.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import (
    AuditAction, CreditMemoStatus, EntityType, InvoiceStatus, LineType,
    PaymentStatus, QBOSyncStatus, VendorBillStatus,
)
from app.models.credit_memo import CreditMemo
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment
from app.models.purchase_order import VendorBill
from app.models.vendor import Vendor
from app.services.base import BaseService
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.qbo_client import QBOClient, QBOError, QBONotConnected
from app.settings_utils import get_setting_value_db

log = logging.getLogger(__name__)

# Strategy-B line-type → generic QBO income item name.
DEFAULT_ITEM_MAP: dict[str, str] = {
    LineType.PRODUCT: "JAKS Parts Sales",
    LineType.MISC: "JAKS Parts Sales",
    LineType.WARRANTY: "JAKS Parts Sales",
    LineType.CORE_CHARGE: "JAKS Core Charge",
    LineType.FREIGHT: "JAKS Freight & Delivery",
    LineType.SHIPPING: "JAKS Freight & Delivery",
    LineType.LOCAL_DELIVERY: "JAKS Freight & Delivery",
    LineType.FUEL_SERVICE_CHARGE: "JAKS Freight & Delivery",
    LineType.RESTOCKING_FEE: "JAKS Fees & Other",
    LineType.MISC_FEE: "JAKS Fees & Other",
    LineType.NSF_FEE: "JAKS Fees & Other",
    LineType.WARRANTY_CREDIT: "JAKS Adjustments",
    LineType.DISCOUNT: "JAKS Adjustments",
}
# Never pushed as invoice lines: cc_surcharge isn't in the invoice total (R1);
# tax is carried via TxnTaxDetail, not a line.
_EXCLUDED_LINE_TYPES = {LineType.CC_SURCHARGE, LineType.TAX}
# The distinct generic items we depend on existing in QBO.
DEFAULT_ITEM_NAMES = sorted(set(DEFAULT_ITEM_MAP.values()))

# §21 — markers Intuit returns when a company on Automated Sales Tax rejects a
# manual TxnTaxDetail override. Used to trigger a no-override retry (let QBO's
# AST compute the tax) instead of failing the push.
_AST_TAX_ERROR_MARKERS = (
    "txntaxdetail", "automated sales tax", "ast", "taxdetail",
    "cannot specify the txn tax", "tax is calculated automatically",
)


def _is_ast_tax_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _AST_TAX_ERROR_MARKERS)
# Invoice statuses we will push (finalized only — never a draft).
_PUSHABLE_STATUSES = {InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID}
# Vendor-bill statuses we will push (3-way-match approved or already paid —
# never a PENDING or DISCREPANCY bill).
_PUSHABLE_BILL_STATUSES = {VendorBillStatus.APPROVED, VendorBillStatus.PAID}

_FALLBACK_ITEM = "JAKS Parts Sales"
# R3 — expense/COGS account vendor-bill lines post to (AccountBasedExpenseLine).
# Owner override via the qbo_bill_expense_account setting; this is the QBO
# default-chart name, same convention as _resolve_income_account's preference.
DEFAULT_BILL_EXPENSE_ACCOUNT = "Cost of Goods Sold"

# Phase 4 — chart-of-accounts mappings the owner sets in Settings → QBO Accounts.
# Stored as ACCOUNT NAMES (resolved by Name at push time, matching the existing
# _resolve_bill_expense_account convention). `used` documents whether today's sync
# already consumes the mapping or it's reserved for the procurement/Bill push phase.
QBO_ACCOUNT_FIELDS: list[dict] = [
    {"key": "qbo_bill_expense_account",    "label": "COGS – Diesel Parts",
     "types": ["Cost of Goods Sold", "Expense"],
     "used": "Active — vendor bills post their cost lines here."},
    {"key": "qbo_income_account",          "label": "Sales Income (parts)",
     "types": ["Income"],
     "used": "Active — the generic income items attach to this account."},
    {"key": "qbo_inventory_asset_account", "label": "Inventory Asset",
     "types": ["Other Current Asset"],
     "used": "Reserved — used when PO/bill inventory posting is enabled."},
    {"key": "qbo_freight_in_account",      "label": "Freight In",
     "types": ["Cost of Goods Sold", "Expense"],
     "used": "Active — vendor-billed freight on a bill posts here (falls back to "
             "the COGS account when unset)."},
    {"key": "qbo_freight_out_account",     "label": "Freight Out / Delivery",
     "types": ["Expense", "Income"],
     "used": "Reserved — used for outbound freight to customers."},
]

# Audit actions for push outcomes. Success reuses the canonical
# AuditAction.QBO_SYNCED; failure is a literal (the action column is a plain
# string — messaging_service sets the same precedent for non-enum actions).
_AUDIT_QBO_SYNC_FAILED = "qbo_sync_failed"
# audit entity_type for vendor bills (EntityType has no VENDOR_BILL member yet;
# lowercase-snake literal matches the house convention).
_ENTITY_VENDOR_BILL = "vendor_bill"


def _q(s: str) -> str:
    """Escape a value for a QBO query string literal (single quotes doubled)."""
    return (s or "").replace("'", "''")


class QBOSyncService(BaseService):
    """R3 — inherits BaseService so pushes carry the REAL acting user
    (self.current_user_id) into mark_synced/mark_sync_failed and into the
    AuditLog rows written for every push outcome. Constructing with no user
    (QBOSyncService(db)) still works and records a system event (user NULL)."""

    def __init__(self, db: Session, current_user_id: int | None = None):
        super().__init__(db, current_user_id=current_user_id)

    # ── push audit trail (R3) ─────────────────────────────────────────────────
    def _audit_push(self, entity_type: str, entity_id: int, *, ok: bool,
                    detail: str) -> None:
        """Record the push outcome (success AND failure) in the audit log,
        attributed to self.current_user_id. NEVER raises — the audit is
        bookkeeping ABOUT the push and must not break the fail-soft contract."""
        try:
            self.audit(
                entity_type=entity_type,
                entity_id=entity_id,
                action=AuditAction.QBO_SYNCED if ok else _AUDIT_QBO_SYNC_FAILED,
                notes=(detail or "")[:480],
            )
            self.db.commit()
        except Exception:
            log.exception("QBO push audit write failed for %s %s", entity_type, entity_id)
            try:
                self.db.rollback()
            except Exception:
                pass

    # ── mapping config ────────────────────────────────────────────────────────
    def item_map(self) -> dict[str, str]:
        """line_type → item name. Owner override via the qbo_item_map JSON
        setting; otherwise the locked default map."""
        raw = get_setting_value_db(self.db, "qbo_item_map", "")
        if raw.strip():
            try:
                override = json.loads(raw)
                if isinstance(override, dict) and override:
                    return {str(k): str(v) for k, v in override.items()}
            except (ValueError, TypeError):
                log.warning("qbo_item_map is not valid JSON — using default map")
        return dict(DEFAULT_ITEM_MAP)

    # ── invoice push ──────────────────────────────────────────────────────────
    def push_invoice(self, invoice_id: int) -> dict:
        """Push one invoice to QBO. Best-effort; never raises.
        Returns {"ok": bool, "qbo_id"|"error"|"skipped": ...}."""
        inv = self.db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if inv is None:
            return {"ok": False, "error": f"Invoice {invoice_id} not found"}
        if inv.qbo_invoice_id:
            return {"ok": True, "skipped": "already synced", "qbo_id": inv.qbo_invoice_id}
        if inv.status not in _PUSHABLE_STATUSES:
            return {"ok": False, "error": f"Invoice is {inv.status}; finalize it before pushing to QBO"}

        try:
            client = QBOClient(self.db)
            item_ids = self._resolve_items(client)
            customer_ref = self._resolve_customer(client, inv.customer)
            payload = self._build_invoice_payload(inv, customer_ref, item_ids)
            try:
                created = client.create("Invoice", payload)
            except QBOError as exc:
                # §21 — Automated Sales Tax: a company with AST rejects our
                # TxnTaxDetail override. Retry once letting QBO compute the tax
                # rather than failing the push outright.
                if _is_ast_tax_error(exc) and "TxnTaxDetail" in payload:
                    log.info("invoice %s: AST tax-override rejected, retrying without override",
                             inv.invoice_number)
                    payload = self._build_invoice_payload(
                        inv, customer_ref, item_ids, include_tax_override=False)
                    created = client.create("Invoice", payload)
                else:
                    raise
            qbo_id = str(created.get("Id", "")).strip()
            if not qbo_id:
                raise QBOError(f"QBO did not return an invoice Id: {created}")
            InvoiceService(self.db, current_user_id=self.current_user_id).mark_synced(invoice_id, qbo_id)
            self._audit_push(EntityType.INVOICE, invoice_id, ok=True,
                             detail=f"Pushed to QBO as Invoice {qbo_id}")
            log.info("invoice %s pushed to QBO as %s", inv.invoice_number, qbo_id)
            return {"ok": True, "qbo_id": qbo_id}
        except QBONotConnected as exc:
            self._audit_push(EntityType.INVOICE, invoice_id, ok=False, detail=str(exc))
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # QBOError or anything unexpected — record, don't raise
            msg = str(exc)[:480]
            log.exception("QBO push failed for invoice %s", invoice_id)
            try:
                InvoiceService(self.db, current_user_id=self.current_user_id).mark_sync_failed(invoice_id, msg)
            except Exception:
                self.db.rollback()
            self._audit_push(EntityType.INVOICE, invoice_id, ok=False, detail=msg)
            return {"ok": False, "error": msg}

    def unsynced_invoice_ids(self, pending_only: bool = False) -> list[int]:
        """IDs of finalized invoices not yet synced to QBO. Used by the bulk-push
        'all unsynced' mode.

        §21 — pending_only=True excludes ERROR-status invoices so a bulk "Sync
        All" doesn't re-hammer Intuit with invoices that already failed (e.g. an
        AST/tax config issue that will just fail again). The background retry
        worker handles ERROR invoices on its own schedule."""
        statuses = (
            [QBOSyncStatus.PENDING] if pending_only
            else None  # everything not synced
        )
        q = self.db.query(Invoice.id).filter(Invoice.status.in_(_PUSHABLE_STATUSES))
        if statuses is not None:
            q = q.filter(Invoice.qbo_sync_status.in_(statuses))
        else:
            q = q.filter(Invoice.qbo_sync_status != QBOSyncStatus.SYNCED)
        return [iid for (iid,) in q.order_by(Invoice.id).all()]

    def failed_invoice_ids(self, max_retries: int = 5) -> list[int]:
        """§21 — ERROR-status invoices still under the retry ceiling, for the
        background retry worker. Past the ceiling they're left for manual review
        so a permanently-broken invoice doesn't loop forever."""
        return [
            iid for (iid,) in (
                self.db.query(Invoice.id)
                .filter(
                    Invoice.status.in_(_PUSHABLE_STATUSES),
                    Invoice.qbo_sync_status == QBOSyncStatus.ERROR,
                    Invoice.qbo_sync_retry_count < max_retries,
                )
                .order_by(Invoice.id)
                .all()
            )
        ]

    def retry_failed_pushes(self, limit: int = 25) -> dict:
        """§21 — re-push ERROR invoices (under the retry ceiling). Returns a
        summary; never raises (per-invoice push is already fail-soft)."""
        ids = self.failed_invoice_ids()[:limit]
        ok = 0
        for iid in ids:
            if self.push_invoice(iid).get("ok"):
                ok += 1
        if ids:
            log.info("QBO retry worker: re-pushed %d/%d failed invoices", ok, len(ids))
        return {"attempted": len(ids), "succeeded": ok}

    # ── payment push ────────────────────────────────────────────────────────
    def push_payment(self, payment_id: int) -> dict:
        """Push one customer payment to QBO as a QBO Payment that LINKS to the
        already-synced invoice(s) it was applied to. Best-effort; never raises.
        Returns {"ok": bool, "qbo_id"|"error"|"skipped": ...}.

        Refuses (fail-soft, recorded via PaymentService.mark_sync_failed) when the
        payment is reversed/NSF, has no active allocations, or targets an invoice
        that is not yet in QBO — in that last case the operator must push the
        invoice FIRST, because the QBO Payment LinkedTxn needs the invoice's
        qbo_invoice_id. Mirrors push_invoice's request/marking structure and the
        money-path invariant: success → mark_synced, failure → mark_sync_failed,
        and nothing here ever mutates the payment's amount, status, or allocations.
        """
        pmt = self.db.query(Payment).filter(Payment.id == payment_id).first()
        if pmt is None:
            return {"ok": False, "error": f"Payment {payment_id} not found"}
        if pmt.qbo_payment_id:
            return {"ok": True, "skipped": "already synced", "qbo_id": pmt.qbo_payment_id}
        if pmt.status != PaymentStatus.APPLIED:
            return self._refuse_payment(
                payment_id,
                f"Payment is {pmt.status}; only an APPLIED payment can be pushed to QuickBooks.",
            )

        active = [a for a in pmt.allocations if not a.is_reversed]
        if not active:
            return self._refuse_payment(
                payment_id,
                "Payment has no active invoice allocations to link — apply it to an "
                "invoice before pushing to QuickBooks.",
            )
        not_in_qbo = [
            (a.invoice.invoice_number if a.invoice else f"invoice {a.invoice_id}")
            for a in active
            if not (a.invoice and a.invoice.qbo_invoice_id)
        ]
        if not_in_qbo:
            return self._refuse_payment(
                payment_id,
                "Push the invoice(s) to QuickBooks first — not yet synced: "
                + ", ".join(not_in_qbo) + ".",
            )
        linked = [(a.invoice.qbo_invoice_id, a.amount_applied) for a in active]

        try:
            client = QBOClient(self.db)
            customer_ref = self._resolve_customer(client, pmt.customer)
            payload = self._build_payment_payload(pmt, customer_ref, linked)
            created = client.create("Payment", payload)
            qbo_id = str(created.get("Id", "")).strip()
            if not qbo_id:
                raise QBOError(f"QBO did not return a payment Id: {created}")
            PaymentService(self.db, current_user_id=self.current_user_id).mark_synced(payment_id, qbo_id)
            self._audit_push(EntityType.PAYMENT, payment_id, ok=True,
                             detail=f"Pushed to QBO as Payment {qbo_id}")
            log.info("payment %s pushed to QBO as %s", payment_id, qbo_id)
            return {"ok": True, "qbo_id": qbo_id}
        except QBONotConnected as exc:
            self._audit_push(EntityType.PAYMENT, payment_id, ok=False, detail=str(exc))
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # QBOError or anything unexpected — record, don't raise
            msg = str(exc)[:480]
            log.exception("QBO push failed for payment %s", payment_id)
            try:
                PaymentService(self.db, current_user_id=self.current_user_id).mark_sync_failed(payment_id, msg)
            except Exception:
                self.db.rollback()
            self._audit_push(EntityType.PAYMENT, payment_id, ok=False, detail=msg)
            return {"ok": False, "error": msg}

    def _refuse_payment(self, payment_id: int, msg: str) -> dict:
        """Pre-flight refusal: record the reason on the payment and return fail-soft.
        Never raises (a failed marking is rolled back and swallowed)."""
        try:
            PaymentService(self.db, current_user_id=self.current_user_id).mark_sync_failed(payment_id, msg)
        except Exception:
            self.db.rollback()
        self._audit_push(EntityType.PAYMENT, payment_id, ok=False, detail=msg)
        return {"ok": False, "error": msg}

    def _build_payment_payload(self, pmt: Payment, customer_ref: dict,
                               linked: list[tuple[str, float]]) -> dict:
        """Build the QBO Payment body: one Line per applied invoice, each carrying a
        LinkedTxn to that invoice's QBO id. TotalAmt is the principal applied (R1 —
        the card surcharge is never part of the invoice/payment principal)."""
        lines: list[dict] = []
        total = 0.0
        for inv_qbo_id, amount in linked:
            amt = round(float(amount), 2)
            if amt == 0:
                continue
            total += amt
            lines.append({
                "Amount": amt,
                "LinkedTxn": [{"TxnId": str(inv_qbo_id), "TxnType": "Invoice"}],
            })
        if not lines:
            raise QBOError(f"Payment {pmt.id} has no non-zero allocations to push")

        payload: dict = {
            "CustomerRef": customer_ref,
            "TotalAmt": round(total, 2),
            "Line": lines,
            "PrivateNote": f"JAKS payment #{pmt.id} ({pmt.payment_method})",
        }
        if getattr(pmt, "check_number", ""):
            payload["PaymentRefNum"] = str(pmt.check_number)[:21]
        return payload

    # ── vendor-bill push (R3) ─────────────────────────────────────────────────
    def push_vendor_bill(self, bill_id: int) -> dict:
        """Push one APPROVED/PAID vendor bill to QBO as a Bill (AP + expense/COGS
        side — without this PAI invoices never enter the books). Best-effort;
        never raises. Returns {"ok": bool, "qbo_id"|"error"|"skipped": ...}.

        Mirrors push_invoice's structure and the money-path invariant: success →
        synced stamp, failure → error stamp, and nothing here ever mutates the
        bill's amounts, status, or 3-way-match state. The vendor is resolved (or
        created) by DisplayName exactly like customers are, including the R2
        multi-match refusal — auto-binding the first of several same-name QBO
        vendors would post AP to the wrong account."""
        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill is None:
            return {"ok": False, "error": f"Vendor bill {bill_id} not found"}
        # Already synced and unchanged → nothing to do. A bill corrected after
        # sync is re-flagged qbo_sync_status=PENDING (Phase 2 edit_vendor_bill),
        # which falls through to an UPDATE below instead of being skipped.
        if bill.qbo_id and bill.qbo_sync_status != QBOSyncStatus.PENDING:
            return {"ok": True, "skipped": "already synced", "qbo_id": bill.qbo_id}
        if bill.status not in _PUSHABLE_BILL_STATUSES:
            return self._refuse_doc(
                VendorBill, bill_id, _ENTITY_VENDOR_BILL,
                f"Vendor bill is {bill.status}; only an APPROVED or PAID bill "
                "can be pushed to QuickBooks.",
            )

        try:
            client = QBOClient(self.db)
            vendor = self.db.query(Vendor).filter(Vendor.id == bill.vendor_id).first()
            vendor_ref = self._resolve_vendor(client, vendor)
            account_id = self._resolve_bill_expense_account(client)
            freight_account_id = self._resolve_freight_in_account(client, account_id)
            payload = self._build_bill_payload(bill, vendor_ref, account_id, freight_account_id)
            if bill.qbo_id:
                # Re-sync of a corrected bill: full update in place (keeps the
                # same QBO Bill, replacing its lines/header with the correction).
                updated = client.update("Bill", bill.qbo_id, payload)
                qbo_id = str(updated.get("Id", "") or bill.qbo_id).strip()
                self._mark_doc_synced(bill, qbo_id)
                self._audit_push(_ENTITY_VENDOR_BILL, bill_id, ok=True,
                                 detail=f"Updated QBO Bill {qbo_id} (correction re-synced)")
                log.info("vendor bill %s updated in QBO as %s", bill_id, qbo_id)
                return {"ok": True, "qbo_id": qbo_id, "updated": True}
            created = client.create("Bill", payload)
            qbo_id = str(created.get("Id", "")).strip()
            if not qbo_id:
                raise QBOError(f"QBO did not return a bill Id: {created}")
            self._mark_doc_synced(bill, qbo_id)
            self._audit_push(_ENTITY_VENDOR_BILL, bill_id, ok=True,
                             detail=f"Pushed to QBO as Bill {qbo_id}")
            log.info("vendor bill %s pushed to QBO as %s", bill_id, qbo_id)
            return {"ok": True, "qbo_id": qbo_id}
        except QBONotConnected as exc:
            self._audit_push(_ENTITY_VENDOR_BILL, bill_id, ok=False, detail=str(exc))
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # QBOError or anything unexpected — record, don't raise
            msg = str(exc)[:480]
            log.exception("QBO push failed for vendor bill %s", bill_id)
            self._mark_doc_failed(VendorBill, bill_id, msg)
            self._audit_push(_ENTITY_VENDOR_BILL, bill_id, ok=False, detail=msg)
            return {"ok": False, "error": msg}

    def _build_bill_payload(self, bill: VendorBill, vendor_ref: dict,
                            account_id: str, freight_account_id: str | None = None) -> dict:
        """QBO Bill body: AccountBasedExpenseLine per bill line, all posting to
        the configured expense/COGS account. Part # + description ride along in
        each line's Description (same convention as the invoice push).

        Vendor-billed freight (bill.freight_amount — freight the vendor put on the
        same invoice as the parts) posts as its own line to the freight-in account
        so the QBO Bill total equals total_amount and matches the vendor invoice."""
        lines: list[dict] = []
        for ln in bill.lines:
            amount = round(float(ln.line_total), 2)
            if amount == 0:
                continue
            desc = ""
            pol = ln.po_line
            if pol is not None:
                desc = pol.description or ""
                sku = getattr(pol.product, "sku", "") if pol.product else ""
                if sku and sku not in desc:
                    desc = f"{sku} — {desc}" if desc else sku
            desc = desc or f"Vendor bill line (qty {ln.qty_billed})"
            lines.append({
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": amount,
                "Description": desc[:1000],
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": account_id},
                },
            })
        freight = round(float(getattr(bill, "freight_amount", 0.0) or 0.0), 2)
        if freight > 0:
            lines.append({
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": freight,
                "Description": "Freight In (vendor-billed)",
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": freight_account_id or account_id},
                },
            })
        if not lines:
            raise QBOError(f"Vendor bill {bill.bill_number or bill.id} has no pushable lines")

        payload: dict = {
            "VendorRef": vendor_ref,
            "Line": lines,
            "PrivateNote": (
                f"JAKS vendor bill #{bill.id}"
                + (f" / PO {bill.po.po_number}" if bill.po else "")
            ),
        }
        if bill.bill_number:
            payload["DocNumber"] = str(bill.bill_number)[:21]
        if bill.bill_date:
            payload["TxnDate"] = bill.bill_date.strftime("%Y-%m-%d")
        if bill.due_date:
            payload["DueDate"] = bill.due_date.strftime("%Y-%m-%d")
        return payload

    # ── credit-memo push (R3) ─────────────────────────────────────────────────
    def push_credit_memo(self, cm_id: int) -> dict:
        """Push one customer credit memo to QBO as a CreditMemo against the
        resolved customer, lines mapped through the same generic-item map as the
        invoice push (incl. CORE_CHARGE via the credited line's original invoice
        line). Best-effort; never raises. Refuses a REVERSED (void-like) CM and
        fails soft when the customer can't be resolved unambiguously."""
        cm = self.db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
        if cm is None:
            return {"ok": False, "error": f"Credit memo {cm_id} not found"}
        if cm.qbo_id:
            return {"ok": True, "skipped": "already synced", "qbo_id": cm.qbo_id}
        if cm.status == CreditMemoStatus.REVERSED:
            return self._refuse_doc(
                CreditMemo, cm_id, EntityType.CREDIT_MEMO,
                f"Credit memo {cm.cm_number} is reversed — it must not be pushed "
                "to QuickBooks.",
            )
        if cm.customer is None:
            return self._refuse_doc(
                CreditMemo, cm_id, EntityType.CREDIT_MEMO,
                f"Credit memo {cm.cm_number} has no customer — cannot push to QuickBooks.",
            )

        try:
            client = QBOClient(self.db)
            item_ids = self._resolve_items(client)
            customer_ref = self._resolve_customer(client, cm.customer)
            payload = self._build_credit_memo_payload(cm, customer_ref, item_ids)
            created = client.create("CreditMemo", payload)
            qbo_id = str(created.get("Id", "")).strip()
            if not qbo_id:
                raise QBOError(f"QBO did not return a credit memo Id: {created}")
            self._mark_doc_synced(cm, qbo_id)
            self._audit_push(EntityType.CREDIT_MEMO, cm_id, ok=True,
                             detail=f"Pushed to QBO as CreditMemo {qbo_id}")
            log.info("credit memo %s pushed to QBO as %s", cm.cm_number, qbo_id)
            return {"ok": True, "qbo_id": qbo_id}
        except QBONotConnected as exc:
            self._audit_push(EntityType.CREDIT_MEMO, cm_id, ok=False, detail=str(exc))
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # QBOError or anything unexpected — record, don't raise
            msg = str(exc)[:480]
            log.exception("QBO push failed for credit memo %s", cm_id)
            self._mark_doc_failed(CreditMemo, cm_id, msg)
            self._audit_push(EntityType.CREDIT_MEMO, cm_id, ok=False, detail=msg)
            return {"ok": False, "error": msg}

    def _build_credit_memo_payload(self, cm: CreditMemo, customer_ref: dict,
                                   item_ids: dict[str, str]) -> dict:
        """QBO CreditMemo body, mirroring _build_invoice_payload's line
        conventions. CM lines have no line_type of their own — when a line
        back-links to the invoice line it credits, that line's type drives the
        item mapping (so a credited core charge lands on 'JAKS Core Charge');
        otherwise it maps as a PRODUCT line."""
        imap = self.item_map()
        push_tax = get_setting_value_db(self.db, "qbo_push_tax", "true").strip().lower() == "true"

        lines: list[dict] = []
        for ln in cm.lines:
            amount = round(
                float(ln.qty) * float(ln.unit_price) * (1 - (ln.discount_pct or 0) / 100), 2
            )
            if amount == 0:
                continue
            line_type = LineType.PRODUCT
            if ln.original_invoice_line_id:
                src = (
                    self.db.query(InvoiceLine)
                    .filter(InvoiceLine.id == ln.original_invoice_line_id)
                    .first()
                )
                if src is not None and src.line_type:
                    line_type = src.line_type
            item_name = imap.get(line_type, _FALLBACK_ITEM)
            item_id = item_ids.get(item_name) or item_ids.get(_FALLBACK_ITEM)
            desc = ln.description or item_name
            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": amount,
                "Description": desc[:1000],
                "SalesItemLineDetail": {
                    "ItemRef": {"value": item_id},
                    "Qty": ln.qty,
                    "UnitPrice": round(float(ln.unit_price), 2),
                    "TaxCodeRef": {"value": "TAX" if ln.is_taxable else "NON"},
                },
            })
        if not lines:
            raise QBOError(f"Credit memo {cm.cm_number} has no pushable lines")

        payload: dict = {
            "CustomerRef": customer_ref,
            "Line": lines,
            "DocNumber": (cm.cm_number or "")[:21],
            "PrivateNote": f"JAKS {cm.cm_number} (id={cm.id})",
            "GlobalTaxCalculation": "TaxExcluded",
        }
        tax_amount = round(float(cm.tax_amount or 0), 2)
        if push_tax and tax_amount > 0:
            payload["TxnTaxDetail"] = {"TotalTax": tax_amount}
        return payload

    # ── shared sync-stamp helpers for QBOSyncMixin docs with a `qbo_id` column ─
    def _mark_doc_synced(self, doc, qbo_id: str) -> None:
        """Same semantics as InvoiceService/PaymentService.mark_synced for
        documents whose entity-id column is the generic `qbo_id` (VendorBill,
        CreditMemo)."""
        doc.qbo_id = qbo_id
        doc.qbo_sync_status = QBOSyncStatus.SYNCED
        doc.qbo_last_synced_at = datetime.utcnow()
        doc.qbo_sync_error = None
        self.db.commit()

    def _mark_doc_failed(self, model, doc_id: int, error: str) -> None:
        """Same semantics as mark_sync_failed; never raises (a failed marking is
        rolled back and swallowed — the fail-soft contract)."""
        try:
            doc = self.db.query(model).filter(model.id == doc_id).first()
            if doc is None:
                return
            doc.qbo_sync_status = QBOSyncStatus.ERROR
            doc.qbo_sync_error = error
            doc.qbo_sync_retry_count += 1
            self.db.commit()
        except Exception:
            self.db.rollback()

    def _refuse_doc(self, model, doc_id: int, entity_type: str, msg: str) -> dict:
        """Pre-flight refusal for VendorBill/CreditMemo pushes: record the reason
        on the document + audit it, return fail-soft. Mirrors _refuse_payment."""
        self._mark_doc_failed(model, doc_id, msg)
        self._audit_push(entity_type, doc_id, ok=False, detail=msg)
        return {"ok": False, "error": msg}

    # ── helpers ───────────────────────────────────────────────────────────────
    def _resolve_items(self, client: QBOClient) -> dict[str, str]:
        """Return {item_name: qbo_item_id} for every generic item we map to.
        Does NOT create items — if any are missing, raise an actionable error so
        the owner runs the one-time 'Set up QBO items' action."""
        names = sorted(set(self.item_map().values()) | {_FALLBACK_ITEM})
        in_list = ", ".join(f"'{_q(n)}'" for n in names)
        rows = client.query(f"select Id, Name from Item where Name in ({in_list})")
        found = {r.get("Name"): str(r.get("Id")) for r in rows if r.get("Id")}
        missing = [n for n in names if n not in found]
        if missing:
            raise QBOError(
                "These QBO income items don't exist yet: "
                + ", ".join(missing)
                + ". Click 'Set up QBO items' in Settings → QuickBooks (one time), then retry."
            )
        return found

    def _resolve_customer(self, client: QBOClient, customer: Customer) -> dict:
        """Return a QBO CustomerRef {"value": id}. Reuses a stored qbo_customer_id,
        else matches by DisplayName, else creates the customer."""
        if customer is None:
            raise QBOError("Invoice has no customer")
        if customer.qbo_customer_id:
            return {"value": customer.qbo_customer_id}

        name = customer.company_name or customer.contact_name or f"Customer {customer.id}"
        rows = client.query(f"select Id, DisplayName from Customer where DisplayName = '{_q(name)}'")

        # Same-name fleet accounts: if more than one QBO customer shares this
        # DisplayName, auto-binding the FIRST one silently posts AR to the wrong
        # account and the wrong-customer binding is then permanent. Refuse instead
        # — fail soft and tell the operator to resolve it manually (set the right
        # qbo_customer_id, or rename one side so the match is unambiguous).
        if len(rows) > 1:
            raise QBOError(
                f"Multiple QBO customers match '{name}' — resolve manually "
                "(link the correct QuickBooks customer to this account, or rename "
                "one so the match is unique), then retry."
            )
        qbo_id = str(rows[0]["Id"]) if rows and rows[0].get("Id") else ""

        if not qbo_id:
            payload: dict = {"DisplayName": name}
            if getattr(customer, "email", ""):
                payload["PrimaryEmailAddr"] = {"Address": customer.email}
            if getattr(customer, "phone", ""):
                payload["PrimaryPhone"] = {"FreeFormNumber": customer.phone}
            created = client.create("Customer", payload)
            qbo_id = str(created.get("Id", "")).strip()
            if not qbo_id:
                raise QBOError(f"Failed to create QBO customer '{name}'")

        customer.qbo_customer_id = qbo_id
        self.db.commit()
        return {"value": qbo_id}

    def _resolve_vendor(self, client: QBOClient, vendor: Vendor | None) -> dict:
        """Return a QBO VendorRef {"value": id} — the vendor-side twin of
        _resolve_customer: match by DisplayName, else create. Applies the SAME
        multi-match refusal: if more than one QBO vendor shares the name,
        auto-binding the first silently posts AP to the wrong account.

        The Vendor model has no qbo_vendor_id column yet, so the binding can't
        be persisted — the name lookup re-runs per push (idempotent: 0 → create,
        1 → reuse). Persist-when-possible is gated on hasattr so a future column
        is picked up without code changes."""
        if vendor is None:
            raise QBOError("Vendor bill has no vendor")
        stored = getattr(vendor, "qbo_vendor_id", "") or ""
        if stored:
            return {"value": stored}

        name = vendor.name or f"Vendor {vendor.id}"
        rows = client.query(
            f"select Id, DisplayName from Vendor where DisplayName = '{_q(name)}'"
        )
        if len(rows) > 1:
            raise QBOError(
                f"Multiple QBO vendors match '{name}' — resolve manually "
                "(rename one side so the match is unique), then retry."
            )
        qbo_id = str(rows[0]["Id"]) if rows and rows[0].get("Id") else ""

        if not qbo_id:
            payload: dict = {"DisplayName": name}
            if getattr(vendor, "email", ""):
                payload["PrimaryEmailAddr"] = {"Address": vendor.email}
            if getattr(vendor, "phone", ""):
                payload["PrimaryPhone"] = {"FreeFormNumber": vendor.phone}
            created = client.create("Vendor", payload)
            qbo_id = str(created.get("Id", "")).strip()
            if not qbo_id:
                raise QBOError(f"Failed to create QBO vendor '{name}'")

        if hasattr(type(vendor), "qbo_vendor_id"):  # future column — persist binding
            vendor.qbo_vendor_id = qbo_id
            self.db.commit()
        return {"value": qbo_id}

    def _resolve_bill_expense_account(self, client: QBOClient) -> str:
        """QBO Account id that vendor-bill expense lines post to. Owner override
        via the qbo_bill_expense_account setting (same settings-driven pattern
        as qbo_item_map / qbo_push_tax); default = the QBO chart's COGS account.
        Does NOT create accounts — missing → actionable error, like _resolve_items."""
        name = (
            get_setting_value_db(self.db, "qbo_bill_expense_account", "").strip()
            or DEFAULT_BILL_EXPENSE_ACCOUNT
        )
        rows = client.query(f"select Id, Name from Account where Name = '{_q(name)}'")
        if rows and rows[0].get("Id"):
            return str(rows[0]["Id"])
        raise QBOError(
            f"QBO expense account '{name}' doesn't exist. Create it in QuickBooks "
            "(or point the qbo_bill_expense_account setting at an existing "
            "expense/COGS account), then retry."
        )

    def _resolve_freight_in_account(self, client: QBOClient, fallback_account_id: str) -> str:
        """QBO Account id that vendor-billed freight posts to. Owner sets it via
        the qbo_freight_in_account setting; when unset (or the named account
        doesn't exist) we fall back to the bill expense account so freight still
        posts to the books rather than blocking the whole bill push over a missing
        freight account."""
        name = get_setting_value_db(self.db, "qbo_freight_in_account", "").strip()
        if not name:
            return fallback_account_id
        rows = client.query(f"select Id, Name from Account where Name = '{_q(name)}'")
        if rows and rows[0].get("Id"):
            return str(rows[0]["Id"])
        return fallback_account_id

    def _build_invoice_payload(self, inv: Invoice, customer_ref: dict,
                               item_ids: dict[str, str],
                               include_tax_override: bool = True) -> dict:
        imap = self.item_map()
        push_tax = get_setting_value_db(self.db, "qbo_push_tax", "true").strip().lower() == "true"
        # §21 — AST companies reject the TxnTaxDetail override; the push layer
        # retries with include_tax_override=False so QBO's own tax engine computes it.
        push_tax = push_tax and include_tax_override

        lines: list[dict] = []
        for ln in sorted(inv.lines, key=lambda x: x.sort_order):
            if ln.line_type in _EXCLUDED_LINE_TYPES:
                continue
            amount = round(float(ln.line_total), 2)
            if amount == 0:
                continue
            item_name = imap.get(ln.line_type, _FALLBACK_ITEM)
            item_id = item_ids.get(item_name) or item_ids.get(_FALLBACK_ITEM)
            desc = ln.description or (ln.product.sku if ln.product else item_name)
            if ln.product and getattr(ln.product, "sku", "") and ln.product.sku not in desc:
                desc = f"{ln.product.sku} — {desc}"
            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": amount,
                "Description": desc[:1000],
                "SalesItemLineDetail": {
                    "ItemRef": {"value": item_id},
                    "Qty": ln.qty,
                    "UnitPrice": round(float(ln.unit_price), 2),
                    "TaxCodeRef": {"value": "TAX" if ln.is_taxable else "NON"},
                },
            })

        if not lines:
            raise QBOError(f"Invoice {inv.invoice_number} has no pushable lines")

        payload: dict = {
            "CustomerRef": customer_ref,
            "Line": lines,
            "DocNumber": (inv.invoice_number or "")[:21],
            "PrivateNote": f"JAKS {inv.invoice_number} (id={inv.id})",
            "GlobalTaxCalculation": "TaxExcluded",
        }
        tax_amount = round(float(inv.tax_amount), 2)
        if push_tax and inv.is_taxable and tax_amount > 0:
            # Override QBO's tax engine with the JAKS-computed amount. If this QBO
            # company uses Automated Sales Tax, it may reject the override — flip
            # the qbo_push_tax setting to false and reconcile tax in QBO instead.
            payload["TxnTaxDetail"] = {"TotalTax": tax_amount}
        return payload

    # ── one-time setup: generic income items ──────────────────────────────────
    def ensure_default_items(self) -> dict:
        """Create any missing generic income items in QBO, all posting to one
        income account. Explicit/admin-run (never auto during a push), because
        creating accounting items is not something to do silently."""
        try:
            client = QBOClient(self.db)
        except QBONotConnected as exc:
            return {"ok": False, "error": str(exc)}

        income_acct = self._resolve_income_account(client)
        if not income_acct:
            return {"ok": False, "error": "No Income account found in QBO to attach items to."}

        existing = {
            r.get("Name") for r in client.query("select Id, Name from Item") if r.get("Name")
        }
        created, already = [], []
        for name in DEFAULT_ITEM_NAMES:
            if name in existing:
                already.append(name)
                continue
            try:
                client.create("Item", {
                    "Name": name,
                    "Type": "Service",
                    "IncomeAccountRef": {"value": income_acct["id"]},
                })
                created.append(name)
            except QBOError as exc:
                return {"ok": False, "error": f"Failed creating item '{name}': {exc}",
                        "created": created, "existing": already}
        return {"ok": True, "created": created, "existing": already,
                "income_account": income_acct["name"]}

    def _resolve_income_account(self, client: QBOClient) -> dict | None:
        rows = client.query("select Id, Name from Account where AccountType = 'Income'")
        if not rows:
            return None
        # Prefer the owner-mapped income account (Settings → QBO Accounts), then
        # the QBO default product-income account, then the first income account.
        preferred = (
            get_setting_value_db(self.db, "qbo_income_account", "").strip()
            or "sales of product income"
        )
        for r in rows:
            if (r.get("Name") or "").strip().lower() == preferred.lower():
                return {"id": str(r["Id"]), "name": r["Name"]}
        return {"id": str(rows[0]["Id"]), "name": rows[0]["Name"]}

    # ── Phase 4: chart-of-accounts mapping (Settings → QBO Accounts) ──────────
    def list_accounts(self) -> dict:
        """Fetch the live QBO chart of accounts for the mapping UI. Read-only.
        Returns {"ok": True, "accounts": [...]} or {"ok": False, "error": str}."""
        try:
            client = QBOClient(self.db)
        except QBONotConnected as exc:
            return {"ok": False, "error": str(exc)}
        try:
            rows = client.query(
                "select Id, Name, AccountType, AccountSubType, Classification "
                "from Account where Active = true order by Name maxresults 1000"
            )
        except Exception as exc:  # noqa: BLE001 — surface a friendly error, never raise
            return {"ok": False, "error": str(exc)[:300]}
        accounts = [{
            "id": str(r.get("Id", "")),
            "name": r.get("Name", ""),
            "type": r.get("AccountType", ""),
            "subtype": r.get("AccountSubType", ""),
            "classification": r.get("Classification", ""),
        } for r in rows if r.get("Name")]
        return {"ok": True, "accounts": accounts}

    def account_mappings(self) -> dict:
        """Current owner-set account names, keyed by setting key (blank = unset)."""
        return {
            f["key"]: get_setting_value_db(self.db, f["key"], "").strip()
            for f in QBO_ACCOUNT_FIELDS
        }

    # ── status for the Settings UI ────────────────────────────────────────────
    def connection_summary(self) -> dict:
        from app.services import qbo_client as qc
        cfg = qc.load_config(self.db)
        counts = dict(
            self.db.query(Invoice.qbo_sync_status, func.count(Invoice.id))
            .group_by(Invoice.qbo_sync_status).all()
        )
        return {
            "connected": cfg.is_connected,
            "has_credentials": cfg.has_credentials,
            "environment": cfg.environment,
            "realm_id": cfg.realm_id,
            "redirect_uri": cfg.redirect_uri,
            "connected_at": get_setting_value_db(self.db, "qbo_connected_at", ""),
            "pending": int(counts.get(QBOSyncStatus.PENDING, 0)),
            "synced": int(counts.get(QBOSyncStatus.SYNCED, 0)),
            "error": int(counts.get(QBOSyncStatus.ERROR, 0)),
        }
