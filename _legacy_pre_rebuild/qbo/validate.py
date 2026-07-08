"""Pre-export validation for QBO CSV / API push.

Returns a structured report of *errors* (block export by default) and
*warnings* (require user acknowledgement) that the UI surfaces in a
modal before generating the file or hitting the live API.

The check set is intentionally conservative: every rule maps to a
real failure mode we've seen during onboarding (missing customer,
$0 line, draft invoice in the queue, line over a sanity threshold,
tax amount with no tax code, voided credit memos, etc.).

The function is pure — no DB access, no Qt — so it can be unit-tested
against synthetic dicts and reused from the period-close wizard (E3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# Lines above this dollar value trip a "double-check this" warning
# (not an error). Tuned to flag obvious typos like an extra zero.
DEFAULT_LINE_AMOUNT_WARNING_THRESHOLD = 10_000.0


@dataclass
class ValidationIssue:
    """One row of the validation report."""
    record_id: object  # invoice_number / po_number / credit id / etc.
    field: str
    message: str
    severity: str  # "error" | "warning"


@dataclass
class ValidationReport:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return bool(self.errors)

    @property
    def total(self) -> int:
        return len(self.errors) + len(self.warnings)

    def add(self, severity: str, record_id, field: str, message: str) -> None:
        issue = ValidationIssue(record_id=record_id, field=field,
                                message=message, severity=severity)
        if severity == "error":
            self.errors.append(issue)
        else:
            self.warnings.append(issue)

    def as_text(self) -> str:
        """Render the report as a copy-pasteable plain-text block."""
        if not self.total:
            return "No issues found."
        out: list[str] = []
        if self.errors:
            out.append(f"ERRORS ({len(self.errors)}):")
            for i in self.errors:
                out.append(f"  [{i.record_id}] {i.field}: {i.message}")
        if self.warnings:
            if out:
                out.append("")
            out.append(f"WARNINGS ({len(self.warnings)}):")
            for i in self.warnings:
                out.append(f"  [{i.record_id}] {i.field}: {i.message}")
        return "\n".join(out)


# ───────────────────────── helpers ─────────────────────────

def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _has_customer(rec: dict) -> bool:
    return bool(
        (rec.get("customer_company") or "").strip()
        or (rec.get("customer_name") or "").strip()
    )


def _has_vendor(rec: dict) -> bool:
    return bool((rec.get("vendor_name") or "").strip())


# ───────────────────────── per-entity rules ─────────────────────────

def _validate_invoice(
    inv: dict,
    report: ValidationReport,
    line_threshold: float,
) -> None:
    rid = inv.get("invoice_number") or inv.get("id") or "?"

    if not _has_customer(inv):
        report.add("error", rid, "customer", "Invoice has no customer.")

    status = (inv.get("status") or "").lower()
    if status == "draft":
        report.add(
            "error", rid, "status",
            "Invoice is still Draft — finalize before exporting to QBO.",
        )
    if status == "void":
        report.add(
            "warning", rid, "status",
            "Invoice is Void; QBO will receive a $0 entry.",
        )

    lines = inv.get("lines") or []
    if not lines:
        report.add("error", rid, "lines", "Invoice has no line items.")

    tax_amount = _f(inv.get("tax_amount"))
    tax_rate = _f(inv.get("tax_rate"))
    if tax_amount > 0 and tax_rate <= 0:
        report.add(
            "warning", rid, "tax_rate",
            "Tax amount > 0 but tax rate is zero — QBO TaxCode mapping "
            "may be wrong.",
        )

    for ln in lines:
        sku = (ln.get("sku") or "").strip()
        qty = _f(ln.get("quantity") or ln.get("qty"))
        rate = _f(ln.get("unit_price"))
        amount = _f(ln.get("line_total")) or round(qty * rate, 2)

        if not sku:
            report.add("error", rid, "line.sku",
                       "Line is missing a SKU / Product code.")
        if qty <= 0:
            report.add("warning", rid, "line.quantity",
                       f"Line {sku or '(no sku)'} has quantity {qty}.")
        if rate <= 0 and amount <= 0:
            report.add("warning", rid, "line.unit_price",
                       f"Line {sku or '(no sku)'} has $0 price.")
        if amount > line_threshold:
            report.add(
                "warning", rid, "line.amount",
                f"Line {sku or '(no sku)'} is ${amount:,.2f} — over the "
                f"${line_threshold:,.0f} sanity threshold.",
            )


def _validate_bill(
    po: dict,
    report: ValidationReport,
    line_threshold: float,
) -> None:
    rid = po.get("po_number") or po.get("id") or "?"

    if not _has_vendor(po):
        report.add("error", rid, "vendor", "PO has no vendor.")

    lines = po.get("lines") or []
    if not lines:
        report.add("error", rid, "lines", "PO has no line items.")

    for ln in lines:
        sku = (ln.get("sku") or "").strip()
        qty = _f(ln.get("quantity_received"))
        if qty <= 0:
            qty = _f(ln.get("quantity_ordered"))
        cost = _f(ln.get("unit_cost"))
        amount = _f(ln.get("line_total")) or round(qty * cost, 2)

        if not sku:
            report.add("error", rid, "line.sku",
                       "PO line is missing a SKU.")
        if qty <= 0:
            report.add("warning", rid, "line.quantity",
                       f"Line {sku or '(no sku)'} has quantity {qty}.")
        if cost <= 0:
            report.add("warning", rid, "line.unit_cost",
                       f"Line {sku or '(no sku)'} has $0 cost.")
        if amount > line_threshold:
            report.add(
                "warning", rid, "line.amount",
                f"Line {sku or '(no sku)'} is ${amount:,.2f} — over the "
                f"${line_threshold:,.0f} sanity threshold.",
            )


def _validate_payment(pay: dict, report: ValidationReport) -> None:
    rid = pay.get("invoice_number") or pay.get("id") or "?"

    if not _has_customer(pay):
        report.add("error", rid, "customer", "Payment has no customer.")
    if not (pay.get("invoice_number") or "").strip():
        report.add("error", rid, "invoice_number",
                   "Payment is not linked to an invoice number.")
    amount = _f(pay.get("amount_paid"))
    if amount <= 0:
        report.add("warning", rid, "amount_paid",
                   f"Payment amount is ${amount:,.2f}; will be skipped on "
                   "export.")


def _validate_credit(credit: dict, report: ValidationReport) -> None:
    rid = credit.get("id") or "?"

    if not _has_customer(credit):
        report.add("error", rid, "customer", "Credit has no customer.")

    status = (credit.get("status") or "").lower()
    if status == "voided":
        report.add("warning", rid, "status",
                   "Credit is Voided — typically excluded from QBO push.")

    amount = _f(credit.get("amount"))
    if amount <= 0:
        report.add("warning", rid, "amount",
                   f"Credit amount is ${amount:,.2f}.")


# ───────────────────────── public entrypoint ─────────────────────────

_KIND_DISPATCH = {
    "invoice": _validate_invoice,
    "bill": _validate_bill,
    "payment": _validate_payment,
    "credit": _validate_credit,
}


def validate_for_export(
    records: Iterable[dict],
    kind: str,
    *,
    line_amount_warning_threshold: float = DEFAULT_LINE_AMOUNT_WARNING_THRESHOLD,
) -> ValidationReport:
    """Run the rule-set for ``kind`` against every record.

    ``kind`` is one of ``"invoice"``, ``"bill"``, ``"payment"``,
    ``"credit"``. Unknown kinds raise ``ValueError``.

    Returns a :class:`ValidationReport`. Callers should treat
    ``report.is_blocking`` as "do not proceed without user override".
    """
    if kind not in _KIND_DISPATCH:
        raise ValueError(f"Unknown validation kind: {kind!r}")

    fn = _KIND_DISPATCH[kind]
    report = ValidationReport()
    for rec in records:
        if kind in ("invoice", "bill"):
            fn(rec, report, line_amount_warning_threshold)
        else:
            fn(rec, report)
    return report
