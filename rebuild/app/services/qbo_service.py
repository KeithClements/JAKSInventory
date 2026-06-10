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

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import InvoiceStatus, LineType, PaymentStatus, QBOSyncStatus
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment
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
# Invoice statuses we will push (finalized only — never a draft).
_PUSHABLE_STATUSES = {InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID}

_FALLBACK_ITEM = "JAKS Parts Sales"


def _q(s: str) -> str:
    """Escape a value for a QBO query string literal (single quotes doubled)."""
    return (s or "").replace("'", "''")


class QBOSyncService:
    def __init__(self, db: Session):
        self.db = db

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
            created = client.create("Invoice", payload)
            qbo_id = str(created.get("Id", "")).strip()
            if not qbo_id:
                raise QBOError(f"QBO did not return an invoice Id: {created}")
            InvoiceService(self.db, current_user_id=1).mark_synced(invoice_id, qbo_id)
            log.info("invoice %s pushed to QBO as %s", inv.invoice_number, qbo_id)
            return {"ok": True, "qbo_id": qbo_id}
        except QBONotConnected as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # QBOError or anything unexpected — record, don't raise
            msg = str(exc)[:480]
            log.exception("QBO push failed for invoice %s", invoice_id)
            try:
                InvoiceService(self.db, current_user_id=1).mark_sync_failed(invoice_id, msg)
            except Exception:
                self.db.rollback()
            return {"ok": False, "error": msg}

    def unsynced_invoice_ids(self) -> list[int]:
        """IDs of finalized invoices not yet synced to QBO (qbo_sync_status !=
        'synced'). Used by the bulk-push 'all unsynced' mode."""
        return [
            iid for (iid,) in (
                self.db.query(Invoice.id)
                .filter(
                    Invoice.status.in_(_PUSHABLE_STATUSES),
                    Invoice.qbo_sync_status != QBOSyncStatus.SYNCED,
                )
                .order_by(Invoice.id)
                .all()
            )
        ]

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
            PaymentService(self.db, current_user_id=1).mark_synced(payment_id, qbo_id)
            log.info("payment %s pushed to QBO as %s", payment_id, qbo_id)
            return {"ok": True, "qbo_id": qbo_id}
        except QBONotConnected as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # QBOError or anything unexpected — record, don't raise
            msg = str(exc)[:480]
            log.exception("QBO push failed for payment %s", payment_id)
            try:
                PaymentService(self.db, current_user_id=1).mark_sync_failed(payment_id, msg)
            except Exception:
                self.db.rollback()
            return {"ok": False, "error": msg}

    def _refuse_payment(self, payment_id: int, msg: str) -> dict:
        """Pre-flight refusal: record the reason on the payment and return fail-soft.
        Never raises (a failed marking is rolled back and swallowed)."""
        try:
            PaymentService(self.db, current_user_id=1).mark_sync_failed(payment_id, msg)
        except Exception:
            self.db.rollback()
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

    def _build_invoice_payload(self, inv: Invoice, customer_ref: dict,
                               item_ids: dict[str, str]) -> dict:
        imap = self.item_map()
        push_tax = get_setting_value_db(self.db, "qbo_push_tax", "true").strip().lower() == "true"

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
        # Prefer the QBO default product-income account when present.
        for r in rows:
            if (r.get("Name") or "").strip().lower() == "sales of product income":
                return {"id": str(r["Id"]), "name": r["Name"]}
        return {"id": str(rows[0]["Id"]), "name": rows[0]["Name"]}

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
