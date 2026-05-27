"""QBO live-API push helpers (D3).

Thin functional layer between the QBO Export Screen UI and
``qbo.integration.QBOIntegrationService``. Each helper takes a list of
already-fetched DB dicts (full invoice/PO with lines), converts them to
the integration service's dataclass shapes, calls the service, and
returns a structured per-record result list.

Why a separate module?
    The UI in ``jaks_inventory.ui.qbo_export_screen`` should not embed
    QBO data-modelling logic. Keeping the conversion + dispatch here
    lets us unit-test it without spinning up Qt and without touching
    real QBO (the integration service is mocked in tests).

All functions return:
    {
        "ok": int,                # successful pushes
        "skipped": int,           # rows skipped (no lines, write disabled,
                                  #   already-exported, etc.)
        "failed": int,            # rows that returned success=False
        "results": [
            {
                "id": <db row id>,
                "status": "ok" | "skipped" | "failed",
                "qbo_id": <str|None>,
                "error": <str|None>,
                "reason": <str|None>,   # for skipped rows
            },
            ...
        ],
    }

The integration service is injected via the ``service`` kwarg so tests
can pass a stub. Production callers leave it None and the helper grabs
``qbo.integration.get_qbo_service()``.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Optional, Any


def _parse_date(value: Any) -> date:
    """Parse a YYYY-MM-DD string / datetime / date into a ``date``.

    Falls back to ``date.today()`` if the value is empty or unparseable —
    QBO requires a date on every invoice/bill, and the UI guards against
    truly missing dates upstream.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if value is None:
        return date.today()
    s = str(value)[:10]
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return date.today()


def _push_summary() -> dict:
    return {"ok": 0, "skipped": 0, "failed": 0, "results": []}


def _record_result(summary: dict, status: str, **fields: Any) -> None:
    summary[status] += 1
    summary["results"].append({"status": status, **fields})


def _stamp_sync_status(table: str | None, summary: dict) -> None:
    """P8 — write ``qbo_sync_status``/``qbo_sync_error`` for each result row.

    Called once at the tail of every ``push_*_to_qbo`` function. The
    per-row status comes from ``summary["results"]``; the table name is
    a constant for each push function (invoices, purchase_orders, …).

    Best-effort — never raises and never modifies the summary, so a
    stamping glitch can't undo a successful QBO write.
    """
    if not table:
        return
    try:
        from qbo.sync_status import stamp as _stamp
    except Exception:
        return
    for res in summary.get("results") or []:
        rid = res.get("id")
        if rid is None:
            continue
        status = res.get("status")
        if status == "ok":
            _stamp(table, rid, "synced", error=None)
        elif status == "failed":
            _stamp(table, rid, "failed", error=str(res.get("error") or "")[:1000])
        elif status == "skipped":
            # Only record "skipped" when the reason is a real QBO refusal
            # (writes disabled, customer missing) — not for cosmetic
            # filtering like zero-amount voids that we never intended to
            # push. Heuristic: any reason that mentions an error.
            reason = (res.get("reason") or "").lower()
            if reason and ("disabled" in reason or "missing" in reason
                           or "not found" in reason):
                _stamp(table, rid, "skipped", error=res.get("reason"))


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

def push_invoices_to_qbo(
    invoices: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push a list of full invoice dicts to QBO via the integration service.

    Each invoice dict must include:
        ``id``, ``invoice_number``, ``invoice_date``, ``due_date``
        (optional), ``customer_name`` / ``customer_company``, ``notes``
        (optional), and ``lines`` — each line must carry
        ``sku``, ``description`` / ``product_title``, ``quantity``,
        ``unit_price``, and optionally ``core_charge``.

    Core-charge lines are emitted as a follow-on ``CORE-CHARGE`` row so
    the QBO ledger separates the deposit. Invoices with zero usable
    lines are skipped.

    ``on_success(invoice_id, qbo_id)`` is invoked once per successful
    push so the caller can stamp ``invoices.qbo_invoice_id`` and call
    ``mark_invoices_exported`` without coupling this module to the DB.
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    # Local import to avoid a hard dependency at module import time.
    from qbo.integration import QBOInvoiceData, QBOInvoiceLine

    summary = _push_summary()
    for inv in invoices:
        inv_id = inv.get("id")
        invoice_number = str(inv.get("invoice_number") or "").strip()
        if not invoice_number:
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason="missing invoice number",
            )
            continue

        customer = (
            (inv.get("customer_company") or "").strip()
            or (inv.get("customer_name") or "").strip()
        )
        if not customer:
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason="missing customer",
            )
            continue

        lines: list[QBOInvoiceLine] = []
        for ln in inv.get("lines") or []:
            sku = (ln.get("sku") or "").strip()
            qty = float(ln.get("quantity") or ln.get("qty") or 0)
            price = float(ln.get("unit_price") or 0)
            if not sku or qty <= 0:
                continue
            desc = (
                ln.get("description")
                or ln.get("product_title")
                or sku
            )
            lines.append(QBOInvoiceLine(
                sku=sku, description=str(desc),
                quantity=qty, unit_price=price,
            ))
            core = float(ln.get("core_charge") or 0)
            if core > 0:
                lines.append(QBOInvoiceLine(
                    sku="CORE-CHARGE",
                    description=f"Core deposit for {sku}",
                    quantity=qty,
                    unit_price=core,
                ))

        if not lines:
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason="no usable line items",
            )
            continue

        data = QBOInvoiceData(
            customer_name=customer,
            invoice_number=invoice_number,
            invoice_date=_parse_date(inv.get("invoice_date")),
            due_date=_parse_date(inv.get("due_date")) if inv.get("due_date") else None,
            lines=lines,
            memo=(inv.get("notes") or "")[:4000],
        )

        try:
            result = service.create_invoice(data, source_id=inv_id)
        except Exception as exc:  # pragma: no cover - defensive
            _record_result(
                summary, "failed",
                id=inv_id, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=inv_id, qbo_id=None,
                error=str(result.get("error") or "unknown error"),
                reason=None,
            )
            continue

        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=inv_id, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and inv_id is not None:
            try:
                on_success(inv_id, qbo_id)
            except Exception:
                # The push itself succeeded — don't reverse the result
                # just because the post-push DB stamp glitched.
                pass

    _stamp_sync_status("invoices", summary)
    return summary


# ---------------------------------------------------------------------------
# Bills (POs)
# ---------------------------------------------------------------------------

def push_bills_to_qbo(
    pos: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push a list of full PO dicts to QBO as Bills.

    Each PO dict must include ``id``, ``po_number``, ``order_date``,
    ``vendor_name``, ``notes`` (optional), and ``lines`` — each line
    must carry ``sku``, ``title`` / ``description``,
    ``quantity_received`` / ``quantity_ordered``, and ``unit_cost``.

    Quantity prefers ``quantity_received`` (the bill should match what
    was actually delivered); falls back to ``quantity_ordered`` when
    received is zero.

    ``on_success(po_id, qbo_id)`` is invoked once per successful push.
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    from qbo.integration import QBOBillData, QBOBillLine

    summary = _push_summary()
    for po in pos:
        po_id = po.get("id")
        po_number = str(po.get("po_number") or "").strip()
        if not po_number:
            _record_result(
                summary, "skipped",
                id=po_id, qbo_id=None, error=None,
                reason="missing PO number",
            )
            continue

        vendor = (po.get("vendor_name") or "").strip()
        if not vendor:
            _record_result(
                summary, "skipped",
                id=po_id, qbo_id=None, error=None,
                reason="missing vendor",
            )
            continue

        lines: list[QBOBillLine] = []
        for ln in po.get("lines") or []:
            sku = (ln.get("sku") or "").strip()
            received = float(ln.get("quantity_received") or 0)
            ordered = float(ln.get("quantity_ordered") or 0)
            qty = received if received > 0 else ordered
            cost = float(ln.get("unit_cost") or 0)
            if not sku or qty <= 0:
                continue
            desc = (
                ln.get("title")
                or ln.get("description")
                or sku
            )
            lines.append(QBOBillLine(
                sku=sku, description=str(desc),
                quantity=qty, unit_cost=cost,
            ))

        if not lines:
            _record_result(
                summary, "skipped",
                id=po_id, qbo_id=None, error=None,
                reason="no usable line items",
            )
            continue

        data = QBOBillData(
            vendor_name=vendor,
            ref_number=po_number,
            bill_date=_parse_date(po.get("order_date")),
            due_date=None,
            lines=lines,
            memo=(po.get("notes") or "")[:4000],
        )

        try:
            linked_po = (po.get("qbo_po_id") or "").strip() or None
            result = service.create_bill(
                data,
                source_id=po_id,
                linked_po_qbo_id=linked_po,
            )
        except Exception as exc:  # pragma: no cover - defensive
            _record_result(
                summary, "failed",
                id=po_id, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=po_id, qbo_id=None,
                error=str(result.get("error") or "unknown error"),
                reason=None,
            )
            continue

        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=po_id, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=po_id, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and po_id is not None:
            try:
                on_success(po_id, qbo_id)
            except Exception:
                pass

    _stamp_sync_status("purchase_orders", summary)
    return summary


# ---------------------------------------------------------------------------
# Payments (D4) — ReceivePayment against the invoice's amount_paid
# ---------------------------------------------------------------------------

def push_payments_to_qbo(
    payments: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push paid invoices to QBO as ReceivePayment records.

    Each input row is the per-invoice payment summary returned by
    ``db.get_paid_invoices_for_export`` — must include ``id``,
    ``invoice_number``, ``invoice_date``, ``amount_paid``, customer
    name/company, and ideally ``qbo_invoice_id`` so the payment can
    link to the QBO Invoice. Rows with no QBO invoice link are still
    pushed (as unapplied credit on the customer) but flagged in the
    result with ``reason="invoice not yet pushed"`` skipped status —
    the user must push the invoice first.

    ``on_success(invoice_id, qbo_payment_id)`` lets the caller stamp
    ``invoices.qbo_payment_id`` after a successful push.
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    from qbo.integration import QBOPaymentData

    summary = _push_summary()
    for pay in payments:
        inv_id = pay.get("id")
        invoice_number = str(pay.get("invoice_number") or "").strip()
        amount = float(pay.get("amount_paid") or 0)
        if not invoice_number:
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason="missing invoice number",
            )
            continue
        if amount <= 0:
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason="zero amount",
            )
            continue

        customer = (
            (pay.get("customer_company") or "").strip()
            or (pay.get("customer_name") or "").strip()
        )
        if not customer:
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason="missing customer",
            )
            continue

        qbo_invoice_id = (pay.get("qbo_invoice_id") or "").strip() or None
        if not qbo_invoice_id:
            # Without the QBO invoice Id we can't link the payment.
            # Refuse rather than create unapplied credit silently.
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason="invoice not yet pushed to QBO",
            )
            continue

        data = QBOPaymentData(
            customer_name=customer,
            invoice_number=invoice_number,
            invoice_qbo_id=qbo_invoice_id,
            payment_date=_parse_date(pay.get("invoice_date")),
            amount=amount,
            payment_method=str(pay.get("payment_method") or ""),
            memo=f"Payment for invoice {invoice_number}",
        )

        try:
            result = service.create_payment(data, source_id=inv_id)
        except Exception as exc:  # pragma: no cover - defensive
            _record_result(
                summary, "failed",
                id=inv_id, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=inv_id, qbo_id=None,
                error=str(result.get("error") or "unknown error"),
                reason=None,
            )
            continue

        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=inv_id, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and inv_id is not None:
            try:
                on_success(inv_id, qbo_id)
            except Exception:
                pass

    return summary


# ---------------------------------------------------------------------------
# Credit memos (D4) — one CreditMemo per customer_credits row
# ---------------------------------------------------------------------------

# Stable per-source SKUs.
#
# P8 — Core credits MUST reference the same ``CORE-CHARGE`` service item
# used on the originating invoice. That way the credit memo's GL impact
# nets cleanly against the Customer Core Deposits liability that the
# invoice posted (instead of creating a separate ``CORE-CREDIT`` line
# that would leave a dangling liability balance in QBO).
_CREDIT_ITEM_BY_SOURCE = {
    "core": ("CORE-CHARGE", "Core return credit"),
    "manual": ("CREDIT", "Customer credit"),
    "return": ("RETURN-CREDIT", "Return credit"),
}


def push_credits_to_qbo(
    credits: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push customer-credit rows to QBO as CreditMemos.

    Each input row is from ``db.get_credit_memos_for_export`` — must
    include ``id``, ``amount``, ``issue_date``, customer name/company,
    and optionally ``source_type``, ``notes``, ``status``. Voided
    credits and credits with non-positive amount are skipped.

    ``on_success(credit_id, qbo_credit_id)`` stamps
    ``customer_credits.qbo_credit_id`` after a successful push.
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    from qbo.integration import QBOCreditMemoData

    summary = _push_summary()
    for c in credits:
        credit_id = c.get("id")
        amount = float(c.get("amount") or 0)
        status = (c.get("status") or "").lower()

        if amount <= 0:
            _record_result(
                summary, "skipped",
                id=credit_id, qbo_id=None, error=None,
                reason="zero amount",
            )
            continue
        if status == "voided":
            _record_result(
                summary, "skipped",
                id=credit_id, qbo_id=None, error=None,
                reason="voided",
            )
            continue

        customer = (
            (c.get("customer_company") or "").strip()
            or (c.get("customer_name") or "").strip()
        )
        if not customer:
            _record_result(
                summary, "skipped",
                id=credit_id, qbo_id=None, error=None,
                reason="missing customer",
            )
            continue

        source = (c.get("source_type") or "manual").lower()
        sku, default_desc = _CREDIT_ITEM_BY_SOURCE.get(
            source, ("CREDIT", "Customer credit")
        )
        desc = (c.get("notes") or "").replace("\n", " ").strip() or default_desc

        data = QBOCreditMemoData(
            customer_name=customer,
            credit_number=f"CM-{credit_id}",
            issue_date=_parse_date(c.get("issue_date")),
            amount=amount,
            sku=sku,
            description=desc,
            memo="",
        )

        try:
            result = service.create_credit_memo(data, source_id=credit_id)
        except Exception as exc:  # pragma: no cover - defensive
            _record_result(
                summary, "failed",
                id=credit_id, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=credit_id, qbo_id=None,
                error=str(result.get("error") or "unknown error"),
                reason=None,
            )
            continue

        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=credit_id, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=credit_id, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and credit_id is not None:
            try:
                on_success(credit_id, qbo_id)
            except Exception:
                pass

    _stamp_sync_status("customer_credits", summary)
    return summary


# ---------------------------------------------------------------------------
# Estimates (Quotes — Phase 5)
# ---------------------------------------------------------------------------

# Maps local ``quotes.status`` values to QBO ``TxnStatus``.
_QUOTE_STATUS_TO_QBO_TXN: dict[str, str] = {
    "draft":     "Pending",
    "sent":      "Pending",
    "accepted":  "Accepted",
    "invoiced":  "Closed",
    "rejected":  "Rejected",
    "lost":      "Rejected",
    "void":      "Rejected",
}


def _quote_status_to_qbo(status: str | None) -> str:
    return _QUOTE_STATUS_TO_QBO_TXN.get(
        (status or "").strip().lower(), "Pending"
    )


def push_quotes_to_qbo(
    quotes: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push a list of full quote dicts to QBO as Estimates.

    Each quote dict must include:
        ``id``, ``quote_number``, ``quote_date``, ``expiration_date``
        (optional), ``customer_name`` / ``customer_company``,
        ``notes`` (optional), ``status`` (optional), and ``lines`` —
        each line must carry ``sku``, ``description`` /
        ``product_title``, ``qty``, ``unit_price``, and optionally
        ``core_charge``.

    ``on_success(quote_id, qbo_id)`` is invoked once per successful
    push so the caller can stamp ``quotes.qbo_estimate_id``.
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    from qbo.integration import QBOEstimateData, QBOEstimateLine

    summary = _push_summary()
    for q in quotes:
        q_id = q.get("id")
        quote_number = str(q.get("quote_number") or "").strip()
        if not quote_number:
            _record_result(
                summary, "skipped",
                id=q_id, qbo_id=None, error=None,
                reason="missing quote number",
            )
            continue

        customer = (
            (q.get("customer_company") or "").strip()
            or (q.get("customer_name") or "").strip()
        )
        if not customer:
            _record_result(
                summary, "skipped",
                id=q_id, qbo_id=None, error=None,
                reason="missing customer",
            )
            continue

        lines: list[QBOEstimateLine] = []
        for ln in q.get("lines") or []:
            sku = (ln.get("sku") or "").strip()
            qty = float(ln.get("qty") or ln.get("quantity") or 0)
            price = float(ln.get("unit_price") or 0)
            if not sku or qty <= 0:
                continue
            desc = (
                ln.get("description")
                or ln.get("product_title")
                or sku
            )
            lines.append(QBOEstimateLine(
                sku=sku, description=str(desc),
                quantity=qty, unit_price=price,
            ))
            core = float(ln.get("core_charge") or 0)
            if core > 0:
                lines.append(QBOEstimateLine(
                    sku="CORE-CHARGE",
                    description=f"Core deposit for {sku}",
                    quantity=qty,
                    unit_price=core,
                ))

        if not lines:
            _record_result(
                summary, "skipped",
                id=q_id, qbo_id=None, error=None,
                reason="no usable line items",
            )
            continue

        data = QBOEstimateData(
            customer_name=customer,
            estimate_number=quote_number,
            estimate_date=_parse_date(q.get("quote_date")),
            expiration_date=_parse_date(q.get("expiration_date"))
                if q.get("expiration_date") else None,
            lines=lines,
            memo=(q.get("notes") or "")[:4000],
            txn_status=_quote_status_to_qbo(q.get("status")),
        )

        try:
            result = service.create_estimate(data, source_id=q_id)
        except Exception as exc:
            _record_result(
                summary, "failed",
                id=q_id, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=q_id, qbo_id=None,
                error=str(result.get("error") or "unknown error"),
                reason=None,
            )
            continue

        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=q_id, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=q_id, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and q_id is not None:
            try:
                on_success(q_id, qbo_id)
            except Exception:
                pass

    return summary




# ---------------------------------------------------------------------------
# PurchaseOrders (Phase 8) - PO push as a *commitment*, separate from Bill
# ---------------------------------------------------------------------------

def push_pos_to_qbo(
    pos: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push a list of full PO dicts to QBO as PurchaseOrders.

    A PurchaseOrder is the *pre-receipt* commitment to a vendor (the
    bill comes later via :func:`push_bills_to_qbo`). The resulting
    QBO id should be stamped onto ``purchase_orders.qbo_po_id`` so
    the eventual Bill can ``LinkedTxn`` back to the PO.

    Quantity comes from ``quantity_ordered`` (PO is a commitment to
    *order*, not what's been received yet).

    ``on_success(po_id, qbo_id)`` lets the caller stamp the crosswalk.
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    from qbo.integration import QBOPurchaseOrderData, QBOPurchaseOrderLine

    summary = _push_summary()
    for po in pos:
        po_id = po.get("id")
        po_number = str(po.get("po_number") or "").strip()
        if not po_number:
            _record_result(
                summary, "skipped",
                id=po_id, qbo_id=None, error=None,
                reason="missing PO number",
            )
            continue

        vendor = (po.get("vendor_name") or "").strip()
        if not vendor:
            _record_result(
                summary, "skipped",
                id=po_id, qbo_id=None, error=None,
                reason="missing vendor",
            )
            continue

        lines: list[QBOPurchaseOrderLine] = []
        for ln in po.get("lines") or []:
            sku = (ln.get("sku") or "").strip()
            qty = float(ln.get("quantity_ordered") or 0)
            cost = float(ln.get("unit_cost") or 0)
            if not sku or qty <= 0:
                continue
            desc = (
                ln.get("title")
                or ln.get("description")
                or sku
            )
            lines.append(QBOPurchaseOrderLine(
                sku=sku, description=str(desc),
                quantity=qty, unit_cost=cost,
            ))

        if not lines:
            _record_result(
                summary, "skipped",
                id=po_id, qbo_id=None, error=None,
                reason="no usable line items",
            )
            continue

        # Closed/received POs should be created in QBO as Closed so
        # they do not show up as "open commitments" in vendor balance.
        local_status = (po.get("status") or "").strip().lower()
        po_status = "Closed" if local_status in ("received", "closed") else "Open"

        data = QBOPurchaseOrderData(
            vendor_name=vendor,
            po_number=po_number,
            po_date=_parse_date(po.get("order_date")),
            due_date=_parse_date(po.get("expected_date"))
                if po.get("expected_date") else None,
            lines=lines,
            memo=(po.get("notes") or "")[:4000],
            ship_to=(po.get("ship_to") or "")[:1000],
            vendor_message=(po.get("vendor_message") or "")[:1000],
            po_status=po_status,
        )

        try:
            result = service.create_purchase_order(data, source_id=po_id)
        except Exception as exc:  # pragma: no cover - defensive
            _record_result(
                summary, "failed",
                id=po_id, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=po_id, qbo_id=None,
                error=str(result.get("error") or "unknown error"),
                reason=None,
            )
            continue

        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=po_id, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=po_id, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and po_id is not None:
            try:
                on_success(po_id, qbo_id)
            except Exception:
                pass

    return summary

# ---------------------------------------------------------------------------
# Sales Receipts (Phase C)
# ---------------------------------------------------------------------------

def push_sales_receipts_to_qbo(
    receipts: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push cash-sale rows to QBO as SalesReceipts.

    Each row is a ``get_invoice_for_export`` style dict where the user
    has flagged ``invoice_kind == 'sales_receipt'``. Lines are required
    just like for invoices.

    ``on_success(invoice_id, qbo_sales_receipt_id)`` stamps
    ``invoices.qbo_sales_receipt_id``.
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    from qbo.integration import QBOSalesReceiptData, QBOInvoiceLine

    summary = _push_summary()
    for inv in receipts:
        inv_id = inv.get("id")
        lines_in = inv.get("lines") or []
        if not lines_in:
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None, reason="no lines",
            )
            continue

        customer = (
            (inv.get("customer_company") or "").strip()
            or (inv.get("customer_name") or "").strip()
        )
        if not customer:
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None, reason="missing customer",
            )
            continue

        sr_lines = [
            QBOInvoiceLine(
                sku=(l.get("sku") or "").strip(),
                description=(l.get("description") or "").strip(),
                quantity=float(l.get("quantity") or 0),
                unit_price=float(l.get("unit_price") or 0),
                qbo_tax_code=l.get("qbo_tax_code"),
            )
            for l in lines_in
        ]

        data = QBOSalesReceiptData(
            customer_name=customer,
            receipt_number=str(inv.get("invoice_number") or f"SR-{inv_id}"),
            receipt_date=_parse_date(inv.get("issue_date") or inv.get("invoice_date")),
            lines=sr_lines,
            payment_method=str(inv.get("payment_method") or ""),
            deposit_to_account_id=inv.get("deposit_to_account"),
            ref_number=str(inv.get("payment_ref") or ""),
            memo=str(inv.get("notes") or "")[:4000],
            qbo_tax_code=inv.get("qbo_tax_code"),
            currency_code=inv.get("currency_code"),
        )

        try:
            result = service.create_sales_receipt(data, source_id=inv_id)
        except Exception as exc:  # pragma: no cover - defensive
            _record_result(
                summary, "failed",
                id=inv_id, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=inv_id, qbo_id=None,
                error=str(result.get("error") or "unknown error"), reason=None,
            )
            continue
        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=inv_id, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=inv_id, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and inv_id is not None:
            try:
                on_success(inv_id, qbo_id)
            except Exception:
                pass

    return summary


# ---------------------------------------------------------------------------
# Refund Receipts (Phase C)
# ---------------------------------------------------------------------------

def push_refunds_to_qbo(
    refunds: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push customer-refund rows to QBO as RefundReceipts.

    Each input row is a customer_credits row marked as a cash refund
    (``source_type == 'refund'`` or ``refund_method`` set). Voided or
    non-positive rows are skipped.

    ``on_success(credit_id, qbo_refund_receipt_id)`` stamps
    ``customer_credits.qbo_refund_receipt_id``.
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    from qbo.integration import QBORefundReceiptData

    summary = _push_summary()
    for r in refunds:
        rid = r.get("id")
        amount = float(r.get("amount") or 0)
        status = (r.get("status") or "").lower()

        if amount <= 0:
            _record_result(
                summary, "skipped",
                id=rid, qbo_id=None, error=None, reason="zero amount",
            )
            continue
        if status == "voided":
            _record_result(
                summary, "skipped",
                id=rid, qbo_id=None, error=None, reason="voided",
            )
            continue

        customer = (
            (r.get("customer_company") or "").strip()
            or (r.get("customer_name") or "").strip()
        )
        if not customer:
            _record_result(
                summary, "skipped",
                id=rid, qbo_id=None, error=None, reason="missing customer",
            )
            continue

        desc = (r.get("notes") or "").replace("\n", " ").strip() or "Customer refund"

        # P8 — when the refund originates from a core return, post it
        # against the same ``CORE-CHARGE`` service item so the cash
        # refund nets against the Customer Core Deposits liability.
        source_type = (r.get("source_type") or "").lower()
        if source_type == "core":
            refund_sku = "CORE-CHARGE"
        else:
            refund_sku = str(r.get("refund_sku") or "REFUND")

        data = QBORefundReceiptData(
            customer_name=customer,
            refund_number=f"RR-{rid}",
            refund_date=_parse_date(r.get("issue_date") or r.get("refund_date")),
            amount=amount,
            sku=refund_sku,
            description=desc,
            payment_method=str(r.get("refund_method") or r.get("payment_method") or ""),
            deposit_from_account_id=r.get("deposit_from_account"),
            ref_number=str(r.get("ref_number") or ""),
            memo="",
        )

        try:
            result = service.create_refund_receipt(data, source_id=rid)
        except Exception as exc:  # pragma: no cover - defensive
            _record_result(
                summary, "failed",
                id=rid, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=rid, qbo_id=None,
                error=str(result.get("error") or "unknown error"), reason=None,
            )
            continue
        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=rid, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=rid, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and rid is not None:
            try:
                on_success(rid, qbo_id)
            except Exception:
                pass

    _stamp_sync_status("customer_credits", summary)
    return summary


# ---------------------------------------------------------------------------
# Vendor Credits (P8) — one VendorCredit per vendor_core_returns row
# ---------------------------------------------------------------------------

def push_vendor_credits_to_qbo(
    vendor_credits: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push vendor-core-return credits to QBO as VendorCredits.

    Each input row should come from ``db.get_vendor_core_credits_for_qbo``
    and include::

        id, vendor_name, qty, core_charge, credit_amount (optional),
        vendor_credit_number (optional), credit_received_date (optional),
        status, qbo_vendor_credit_id (already-synced rows are skipped),
        linked_bill_qbo_id (optional — purchase_orders.qbo_bill_id).

    Rows with ``status != 'credited'`` or already-set
    ``qbo_vendor_credit_id`` are skipped. The credit amount is
    ``credit_amount`` if set, else ``qty * core_charge``.

    ``on_success(vcr_id, qbo_vendor_credit_id)`` stamps
    ``vendor_core_returns.qbo_vendor_credit_id`` and clears the
    ``qbo_sync_*`` columns (via the centralized
    :func:`_stamp_sync_status` post-loop pass).
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    from qbo.integration import QBOVendorCreditData

    summary = _push_summary()
    for r in vendor_credits:
        vcr_id = r.get("id")
        status = (r.get("status") or "").lower()
        if status != "credited":
            _record_result(
                summary, "skipped",
                id=vcr_id, qbo_id=None, error=None,
                reason=f"status={status or 'unknown'}",
            )
            continue
        if r.get("qbo_vendor_credit_id"):
            _record_result(
                summary, "skipped",
                id=vcr_id, qbo_id=str(r.get("qbo_vendor_credit_id")),
                error=None, reason="already synced",
            )
            continue

        qty = float(r.get("qty") or 0)
        per_unit = float(r.get("core_charge") or 0)
        amount = float(r.get("credit_amount") or 0) or round(qty * per_unit, 2)
        if amount <= 0:
            _record_result(
                summary, "skipped",
                id=vcr_id, qbo_id=None, error=None, reason="zero amount",
            )
            continue

        vendor = (r.get("vendor_name") or "").strip()
        if not vendor:
            _record_result(
                summary, "skipped",
                id=vcr_id, qbo_id=None, error=None, reason="missing vendor",
            )
            continue

        data = QBOVendorCreditData(
            vendor_name=vendor,
            credit_number=str(r.get("vendor_credit_number") or f"VC-{vcr_id}")[:21],
            txn_date=_parse_date(r.get("credit_received_date") or r.get("updated_at")),
            amount=amount,
            quantity=qty or 1,
            description=(r.get("notes") or "Vendor core return credit").replace("\n", " ")[:4000],
            linked_bill_qbo_id=r.get("linked_bill_qbo_id") or r.get("qbo_bill_id"),
            memo="",
        )

        try:
            result = service.create_vendor_credit(data, source_id=vcr_id)
        except Exception as exc:  # pragma: no cover - defensive
            _record_result(
                summary, "failed",
                id=vcr_id, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=vcr_id, qbo_id=None,
                error=str(result.get("error") or "unknown error"), reason=None,
            )
            continue
        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=vcr_id, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=vcr_id, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and vcr_id is not None:
            try:
                on_success(vcr_id, qbo_id)
            except Exception:
                pass

    _stamp_sync_status("vendor_core_returns", summary)
    return summary


# ---------------------------------------------------------------------------
# Bill Payments (Phase C)
# ---------------------------------------------------------------------------

def push_bill_payments_to_qbo(
    payments: Iterable[dict],
    *,
    service: Any = None,
    on_success: Optional[callable] = None,
) -> dict:
    """Push bill-payment rows to QBO as BillPayments.

    Each row must include ``id``, ``vendor_name``, ``amount``,
    ``payment_date``, and either ``bill_qbo_ids`` (list of QBO Bill ids)
    or ``po_ids`` (the wrapper resolves them to ``qbo_bill_id`` via
    ``purchase_orders``). Rows with no resolvable bills are skipped.

    ``on_success(payment_id, qbo_bill_payment_id)`` stamps
    ``bill_payments.qbo_bill_payment_id``.
    """
    if service is None:
        from qbo.integration import get_qbo_service
        service = get_qbo_service()

    from qbo.integration import QBOBillPaymentData

    summary = _push_summary()
    for p in payments:
        pid = p.get("id")
        amount = float(p.get("amount") or 0)
        if amount <= 0:
            _record_result(
                summary, "skipped",
                id=pid, qbo_id=None, error=None, reason="zero amount",
            )
            continue

        vendor = (p.get("vendor_name") or "").strip()
        if not vendor:
            _record_result(
                summary, "skipped",
                id=pid, qbo_id=None, error=None, reason="missing vendor",
            )
            continue

        bill_ids = list(p.get("bill_qbo_ids") or [])
        if not bill_ids and p.get("po_ids"):
            try:
                from db.database import get_db
                db = get_db()
                placeholders = ",".join("?" for _ in p["po_ids"])
                rows = db.execute(
                    f"SELECT qbo_bill_id FROM purchase_orders WHERE id IN ({placeholders})",
                    tuple(p["po_ids"]),
                ).fetchall()
                bill_ids = [str(r[0]) for r in rows if r and r[0]]
            except Exception:
                bill_ids = []

        if not bill_ids:
            _record_result(
                summary, "skipped",
                id=pid, qbo_id=None, error=None, reason="no linked bills",
            )
            continue

        data = QBOBillPaymentData(
            vendor_name=vendor,
            payment_number=str(p.get("payment_number") or f"BP-{pid}"),
            payment_date=_parse_date(p.get("payment_date")),
            amount=amount,
            bill_qbo_ids=bill_ids,
            pay_type=str(p.get("pay_type") or "Check"),
            bank_account_id=p.get("bank_account_id"),
            ref_number=str(p.get("ref_number") or ""),
            memo=str(p.get("memo") or "")[:4000],
        )

        try:
            result = service.create_bill_payment(data, source_id=pid)
        except Exception as exc:  # pragma: no cover - defensive
            _record_result(
                summary, "failed",
                id=pid, qbo_id=None, error=str(exc), reason=None,
            )
            continue

        if not result.get("success"):
            _record_result(
                summary, "failed",
                id=pid, qbo_id=None,
                error=str(result.get("error") or "unknown error"), reason=None,
            )
            continue
        if result.get("action") == "skipped":
            _record_result(
                summary, "skipped",
                id=pid, qbo_id=None, error=None,
                reason=str(result.get("reason") or "writes disabled"),
            )
            continue

        qbo_id = str(result.get("qbo_id") or "")
        _record_result(
            summary, "ok",
            id=pid, qbo_id=qbo_id, error=None, reason=None,
        )
        if on_success and pid is not None:
            try:
                on_success(pid, qbo_id)
            except Exception:
                pass

    return summary
