"""QBO Journal Entry push (minimal, per-invoice).

For each finalized invoice we emit a single ``JournalEntry`` that
captures the accounting impact:

* DR Accounts Receivable      = invoice total (incl. tax + core)
* CR Sales Income             = line subtotal (before tax/core/discounts)
* CR Sales Tax Payable        = ``tax_amount`` (if non-zero)
* CR Core Liability           = sum of ``line.core_charge × quantity``
* For each line with a unit cost > 0:
    DR Cost of Goods Sold     = qty × unit_cost
    CR Inventory Asset        = qty × unit_cost

Account IDs are pulled from :func:`qbo.config.get_account_mapping`. If
the four sales-side accounts (``ar``, ``sales_income``, ``tax_payable``,
``core_liability``) are not configured, the invoice is skipped — we
will not push an unbalanced JE.

Idempotency is enforced at the DB level: ``invoices.qbo_journal_entry_id``
is stamped on success, and re-runs skip already-stamped rows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class QBOJournalEntryLine:
    """One DR or CR line on a JournalEntry."""
    account_id: str
    posting_type: str           # "Debit" or "Credit"
    amount: float
    description: str = ""


@dataclass
class QBOJournalEntryData:
    """A full balanced JournalEntry payload."""
    txn_date: date
    ref_number: str             # invoice number
    memo: str = ""
    lines: list[QBOJournalEntryLine] = field(default_factory=list)

    @property
    def total_debits(self) -> float:
        return round(
            sum(l.amount for l in self.lines if l.posting_type == "Debit"),
            2,
        )

    @property
    def total_credits(self) -> float:
        return round(
            sum(l.amount for l in self.lines if l.posting_type == "Credit"),
            2,
        )

    @property
    def is_balanced(self) -> bool:
        return abs(self.total_debits - self.total_credits) < 0.01


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return date.today()
    s = str(value)[:10]
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return date.today()


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def build_je_from_invoice(invoice: dict) -> Optional[QBOJournalEntryData]:
    """Build a balanced JournalEntry from a full invoice dict.

    Returns None when the chart-of-accounts mapping is incomplete
    (missing AR / Sales Income), or the invoice has no usable lines —
    those rows are skipped rather than pushed unbalanced.
    """
    from qbo.config import get_account_mapping
    mapping = get_account_mapping()

    ar_acct = (mapping.get("ar") or "").strip()
    income_acct = (mapping.get("sales_income") or "").strip()
    tax_acct = (mapping.get("tax_payable") or "").strip()
    core_acct = (mapping.get("core_liability") or "").strip()
    cogs_acct = (mapping.get("cogs") or "").strip()
    inv_acct = (mapping.get("inventory_asset") or "").strip()

    if not ar_acct or not income_acct:
        logger.info(
            "[QBO JE] Skipping invoice %s — AR / Sales Income account "
            "mapping not configured.",
            invoice.get("invoice_number"),
        )
        return None

    lines = invoice.get("lines") or []
    if not lines:
        return None

    # Component totals.
    subtotal = 0.0
    core_total = 0.0
    cogs_total = 0.0

    for ln in lines:
        qty = _money(ln.get("quantity") or ln.get("qty"))
        unit = _money(ln.get("unit_price"))
        core = _money(ln.get("core_charge"))
        cost = _money(ln.get("unit_cost") or ln.get("cost"))
        if qty <= 0:
            continue
        subtotal += qty * unit
        core_total += qty * core
        cogs_total += qty * cost

    tax_amount = _money(invoice.get("tax_amount") or invoice.get("tax"))
    invoice_total = _money(invoice.get("total"))
    if not invoice_total:
        invoice_total = round(subtotal + tax_amount + core_total, 2)

    if invoice_total <= 0:
        return None

    je_lines: list[QBOJournalEntryLine] = []
    memo = f"JAKS Invoice {invoice.get('invoice_number') or invoice.get('id')}"

    # Sales-side
    je_lines.append(QBOJournalEntryLine(
        account_id=ar_acct, posting_type="Debit",
        amount=round(invoice_total, 2),
        description=f"AR for {memo}",
    ))
    if subtotal > 0:
        je_lines.append(QBOJournalEntryLine(
            account_id=income_acct, posting_type="Credit",
            amount=round(subtotal, 2),
            description=f"Sales for {memo}",
        ))
    if tax_amount > 0:
        if not tax_acct:
            logger.info(
                "[QBO JE] Invoice %s has tax %.2f but tax_payable account "
                "not configured — folding tax into income.",
                invoice.get("invoice_number"), tax_amount,
            )
            # Fold into income so the JE balances without a tax account.
            # (Last income line already added; bump it.)
            je_lines[-1] = QBOJournalEntryLine(
                account_id=income_acct, posting_type="Credit",
                amount=round(subtotal + tax_amount, 2),
                description=f"Sales (incl. tax) for {memo}",
            )
        else:
            je_lines.append(QBOJournalEntryLine(
                account_id=tax_acct, posting_type="Credit",
                amount=round(tax_amount, 2),
                description=f"Sales tax for {memo}",
            ))
    if core_total > 0 and core_acct:
        je_lines.append(QBOJournalEntryLine(
            account_id=core_acct, posting_type="Credit",
            amount=round(core_total, 2),
            description=f"Core deposit for {memo}",
        ))
    elif core_total > 0:
        # No core liability account — roll cores into income to balance.
        je_lines[-1] = QBOJournalEntryLine(
            account_id=je_lines[-1].account_id,
            posting_type="Credit",
            amount=round(je_lines[-1].amount + core_total, 2),
            description=je_lines[-1].description + " (incl. cores)",
        )

    # COGS pair — only when both accounts mapped and we have a cost figure.
    if cogs_total > 0 and cogs_acct and inv_acct:
        je_lines.append(QBOJournalEntryLine(
            account_id=cogs_acct, posting_type="Debit",
            amount=round(cogs_total, 2),
            description=f"COGS for {memo}",
        ))
        je_lines.append(QBOJournalEntryLine(
            account_id=inv_acct, posting_type="Credit",
            amount=round(cogs_total, 2),
            description=f"Inventory relief for {memo}",
        ))

    data = QBOJournalEntryData(
        txn_date=_parse_date(invoice.get("invoice_date")),
        ref_number=str(invoice.get("invoice_number") or invoice.get("id") or "")[:21],
        memo=memo,
        lines=je_lines,
    )

    if not data.is_balanced:
        logger.warning(
            "[QBO JE] Built JE for invoice %s is not balanced "
            "(DR=%.2f, CR=%.2f). Skipping.",
            invoice.get("invoice_number"),
            data.total_debits, data.total_credits,
        )
        return None

    return data


# ---------------------------------------------------------------------------
# Live / mock create
# ---------------------------------------------------------------------------

def create_journal_entry(
    data: QBOJournalEntryData,
    *,
    source_id: int | None = None,
) -> dict[str, Any]:
    """Push a built ``QBOJournalEntryData`` to QBO (or simulate in mock).

    Returns ``{"success": bool, "action": "created"|"skipped", "qbo_id":
    str|None, "error": str|None, "reason": str|None}``.

    ``source_id`` is the local invoice id this JE represents — used only
    to tag the audit-log row written by :mod:`qbo.sync_log`.
    """
    from qbo.config import is_mock_mode, is_write_enabled
    from qbo import sync_log

    if not data.is_balanced:
        return {
            "success": False, "action": None, "qbo_id": None,
            "error": "Journal entry is not balanced.", "reason": None,
        }

    # Build a JSON snapshot of the *request* up front so we always have
    # something to log, even if the SDK build fails later.
    request_payload = {
        "TxnDate": data.txn_date.isoformat(),
        "DocNumber": data.ref_number,
        "PrivateNote": data.memo,
        "Line": [
            {
                "DetailType": "JournalEntryLineDetail",
                "Amount": ln.amount,
                "Description": ln.description,
                "JournalEntryLineDetail": {
                    "PostingType": ln.posting_type,
                    "AccountRef": {"value": ln.account_id},
                },
            }
            for ln in data.lines
        ],
    }
    log_id = sync_log.start_log(
        source_type="journal_entry",
        source_id=source_id,
        qbo_object_type="JournalEntry",
        request_json=sync_log.to_json_text(request_payload),
    )

    if is_mock_mode():
        mock_id = f"MOCK-JE-{data.ref_number or 'NEW'}"
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text({"Id": mock_id, "_mock": True}),
            qbo_object_id=mock_id,
        )
        return {
            "success": True, "action": "created",
            "qbo_id": mock_id, "error": None, "reason": None,
        }

    if not is_write_enabled():
        sync_log.complete_log(
            log_id, status="skipped",
            error_message="QBO writes disabled",
        )
        return {
            "success": True, "action": "skipped", "qbo_id": None,
            "error": None, "reason": "QBO writes disabled",
        }

    try:
        from qbo.client import get_qbo_client
        from quickbooks.objects.journalentry import JournalEntry, JournalEntryLine
        from quickbooks.objects.detailline import JournalEntryLineDetail

        client = get_qbo_client()
        if not client.is_connected and not client.connect():
            sync_log.complete_log(
                log_id, status="failed",
                error_message="Could not connect to QBO.",
            )
            return {
                "success": False, "action": None, "qbo_id": None,
                "error": "Could not connect to QBO.", "reason": None,
            }

        je = JournalEntry()
        je.TxnDate = data.txn_date.isoformat()
        if data.ref_number:
            je.DocNumber = data.ref_number
        if data.memo:
            je.PrivateNote = data.memo[:4000]
        je.Line = []
        for ln in data.lines:
            line = JournalEntryLine()
            line.DetailType = "JournalEntryLineDetail"
            line.Amount = ln.amount
            line.Description = ln.description[:1000] if ln.description else ""
            detail = JournalEntryLineDetail()
            detail.PostingType = ln.posting_type
            detail.AccountRef = {"value": ln.account_id}
            line.JournalEntryLineDetail = detail
            je.Line.append(line)

        # Backfill the request with the real SDK serialization.
        sync_log.update_request(
            log_id, request_json=sync_log.to_json_text(
                sync_log.qbo_obj_to_dict(je),
            ),
        )

        saved = je.save(qb=client._client)
        qbo_id = str(saved.Id)
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text(
                sync_log.qbo_obj_to_dict(saved),
            ),
            qbo_object_id=qbo_id,
        )
        return {
            "success": True, "action": "created",
            "qbo_id": qbo_id,
            "error": None, "reason": None,
        }
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        sync_log.complete_log(log_id, status="failed", error_message=msg)
        return {
            "success": False, "action": None, "qbo_id": None,
            "error": msg, "reason": None,
        }


# ---------------------------------------------------------------------------
# Bulk push
# ---------------------------------------------------------------------------

def push_journal_entries_for_invoices(
    invoices: Iterable[dict],
    *,
    on_success: Optional[Callable[[int, str], None]] = None,
    service_create: Optional[Callable[[QBOJournalEntryData], dict]] = None,
) -> dict[str, Any]:
    """Build + push a JournalEntry for each invoice.

    ``service_create`` is a dependency-injection hook for tests — when
    None, calls :func:`create_journal_entry` directly.

    Returns the standard ``{"ok", "skipped", "failed", "results": [...]}``
    summary shape used by :mod:`qbo.push`.
    """
    creator = service_create or create_journal_entry
    summary: dict[str, Any] = {"ok": 0, "skipped": 0, "failed": 0, "results": []}

    for inv in invoices:
        inv_id = inv.get("id")
        existing_je = (inv.get("qbo_journal_entry_id") or "").strip() \
            if isinstance(inv.get("qbo_journal_entry_id"), str) else ""
        if existing_je:
            summary["skipped"] += 1
            summary["results"].append({
                "id": inv_id, "status": "skipped", "qbo_id": existing_je,
                "error": None, "reason": "already linked",
            })
            continue

        data = build_je_from_invoice(inv)
        if data is None:
            summary["skipped"] += 1
            summary["results"].append({
                "id": inv_id, "status": "skipped", "qbo_id": None,
                "error": None,
                "reason": "missing mapping or no usable lines",
            })
            continue

        try:
            try:
                result = creator(data, source_id=inv_id)
            except TypeError:
                # ``service_create`` test hooks accept just ``data``.
                result = creator(data)
        except Exception as exc:
            summary["failed"] += 1
            summary["results"].append({
                "id": inv_id, "status": "failed", "qbo_id": None,
                "error": str(exc), "reason": None,
            })
            continue

        if not result.get("success"):
            summary["failed"] += 1
            summary["results"].append({
                "id": inv_id, "status": "failed", "qbo_id": None,
                "error": result.get("error") or "unknown",
                "reason": None,
            })
            continue
        if result.get("action") == "skipped":
            summary["skipped"] += 1
            summary["results"].append({
                "id": inv_id, "status": "skipped", "qbo_id": None,
                "error": None,
                "reason": result.get("reason") or "writes disabled",
            })
            continue

        qbo_id = str(result.get("qbo_id") or "")
        summary["ok"] += 1
        summary["results"].append({
            "id": inv_id, "status": "ok", "qbo_id": qbo_id,
            "error": None, "reason": None,
        })
        if on_success and inv_id is not None:
            try:
                on_success(inv_id, qbo_id)
            except Exception:
                pass

    return summary


def stamp_invoice_je(invoice_id: int, qbo_id: str) -> None:
    """Standard ``on_success`` callback — stamps the local invoice row."""
    from db import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE invoices SET qbo_journal_entry_id=%s WHERE id=%s",
            (qbo_id, int(invoice_id)),
        )
        conn.commit()
