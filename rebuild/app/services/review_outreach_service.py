"""
app/services/review_outreach_service.py
=======================================
Review-seeding kit (cold-start social proof).

The JAK's Diesel storefront currently has ~0 customer reviews while competitors
run 800-1,200 (ATL ~875 @ 4.5*, Highway & Heavy Parts 1,200+ @ 4.6*). The
fastest *legitimate* way to seed the first wave is to ask the customers who
already bought — the phone/quote sales sitting in this ERP that never triggered
a Shopify post-purchase review email.

This service turns finalized sales history into two outreach artifacts:

  1. ``judgeme_rows()``  -> rows for Judge.me's "send review requests via CSV"
     importer (product / on-site reviews + the AggregateRating schema the theme
     already emits). One row per (customer, Shopify-linked product) the customer
     actually bought, deduped to the most recent purchase.

  2. ``google_outreach_rows()`` -> one row per customer for the manual Google
     Business Profile / personal-ask track (company reviews). No product needed.

Consent (R12) is respected on BOTH tracks: ``do_not_contact`` and opted-out
customers are always excluded; the email track requires ``allow_email``; SMS
eligibility (``allow_sms`` + a consent timestamp) is surfaced per row so the
owner only texts customers who agreed to it.

Judge.me CSV contract (verified against judge.me help, 2026-06-15):
  reviewer_name, reviewer_email, (product_id | product_handle), fulfilled_at
  in ``dd/mm/yyyy``, optional quantity, optional processed_at. We emit
  ``product_id`` (the numeric Shopify product id parsed from the stored GID).
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import joinedload

from app.constants import InvoiceStatus, LineType
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.services.base import BaseService


class ReviewOutreachService(BaseService):
    """Builds review-request outreach lists from finalized sales history."""

    # A completed sale (goods went out the door): finalized, not draft, not void.
    _SOLD_STATUSES = (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID)

    # ── Public API ────────────────────────────────────────────────────────────

    def judgeme_rows(self, *, paid_only: bool = True, since: datetime | None = None) -> list[dict]:
        """Rows for Judge.me's CSV review-request importer.

        One row per (customer email, Shopify product), deduped to the customer's
        most recent purchase of that product. Only PRODUCT lines on
        Shopify-linked products with an email-eligible customer are included.
        """
        best: dict[tuple[str, str], dict] = {}

        for inv in self._candidate_invoices(paid_only=paid_only, since=since):
            cust = inv.customer
            if not self._email_ok(cust):
                continue
            email = cust.email.strip()
            fulfilled = inv.created_at or datetime.utcnow()

            for line in inv.lines:
                if line.line_type != LineType.PRODUCT:
                    continue
                product = line.product
                if product is None:
                    continue
                pid = self._shopify_numeric_id(product.shopify_product_id)
                if not pid:
                    continue  # not linked to Shopify yet -> can't target a review

                key = (email.lower(), pid)
                rec = best.get(key)
                if rec is None or fulfilled > rec["_dt"]:
                    best[key] = {
                        "reviewer_name": cust.display_name,
                        "reviewer_email": email,
                        "product_id": pid,
                        "fulfilled_at": fulfilled.strftime("%d/%m/%Y"),
                        "quantity": int(line.qty or 1),
                        "_dt": fulfilled,
                    }

        rows = sorted(best.values(), key=lambda r: (r["reviewer_email"], r["product_id"]))
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    def google_outreach_rows(self, *, paid_only: bool = True, since: datetime | None = None) -> list[dict]:
        """One row per customer for the manual Google / personal-ask track.

        Aggregates each customer's finalized orders (count, total spent, most
        recent purchase, a sample product to personalize the ask). Excludes
        do_not_contact / opted-out customers and anyone with no usable channel.
        Sorted by total spent desc (ask your best customers first).
        """
        agg: dict[int, dict] = {}

        for inv in self._candidate_invoices(paid_only=paid_only, since=since):
            cust = inv.customer
            if cust is None or cust.do_not_contact or cust.opt_out_at is not None:
                continue
            email_ok = self._email_ok(cust)
            sms_ok = self._sms_ok(cust)
            if not (email_ok or sms_ok):
                continue

            created = inv.created_at or datetime.utcnow()
            rec = agg.get(cust.id)
            if rec is None:
                rec = agg[cust.id] = {
                    "customer_name": cust.contact_name or cust.display_name,
                    "company_name": cust.company_name or "",
                    "email": cust.email if email_ok else "",
                    "phone": cust.phone if sms_ok else "",
                    "allow_email": "yes" if email_ok else "no",
                    "allow_sms": "yes" if sms_ok else "no",
                    "orders": 0,
                    "total_spent": 0.0,
                    "_last_dt": created,
                    "sample_product": "",
                }
            rec["orders"] += 1
            rec["total_spent"] += inv.total
            if created >= rec["_last_dt"]:
                rec["_last_dt"] = created
            if not rec["sample_product"]:
                rec["sample_product"] = self._sample_product_name(inv)

        out: list[dict] = []
        for rec in agg.values():
            out.append({
                "customer_name": rec["customer_name"],
                "company_name": rec["company_name"],
                "email": rec["email"],
                "phone": rec["phone"],
                "allow_email": rec["allow_email"],
                "allow_sms": rec["allow_sms"],
                "orders": rec["orders"],
                "total_spent": f"{rec['total_spent']:.2f}",
                "last_purchase": rec["_last_dt"].strftime("%Y-%m-%d"),
                "sample_product": rec["sample_product"],
            })
        out.sort(key=lambda r: (-float(r["total_spent"]), r["company_name"].lower()))
        return out

    # ── Internals ───────────────────────────────────────────────────────────--

    def _candidate_invoices(self, *, paid_only: bool, since: datetime | None) -> list[Invoice]:
        statuses = (InvoiceStatus.PAID,) if paid_only else self._SOLD_STATUSES
        q = (
            self.db.query(Invoice)
            .filter(Invoice.status.in_(statuses))
            .options(
                joinedload(Invoice.customer),
                joinedload(Invoice.lines).joinedload(InvoiceLine.product),
            )
            .order_by(Invoice.created_at.asc())  # oldest first; dedupe keeps newest
        )
        if since is not None:
            q = q.filter(Invoice.created_at >= since)
        return q.all()

    @staticmethod
    def _sample_product_name(inv: Invoice) -> str:
        for line in inv.lines:
            if line.line_type != LineType.PRODUCT:
                continue
            if line.product is not None and line.product.title:
                return line.product.title
            if line.description:
                return line.description
        return ""

    @staticmethod
    def _shopify_numeric_id(gid: str | None) -> str:
        """``gid://shopify/Product/123`` -> ``123``; tolerates a bare numeric."""
        if not gid:
            return ""
        m = re.search(r"(\d+)\s*$", str(gid))
        return m.group(1) if m else ""

    @staticmethod
    def _email_ok(c: Customer | None) -> bool:
        return bool(
            c and (c.email or "").strip()
            and not c.do_not_contact
            and c.allow_email
            and c.opt_out_at is None
        )

    @staticmethod
    def _sms_ok(c: Customer | None) -> bool:
        return bool(
            c and (c.phone or "").strip()
            and not c.do_not_contact
            and c.allow_sms
            and c.sms_consent_at is not None
            and c.opt_out_at is None
        )
