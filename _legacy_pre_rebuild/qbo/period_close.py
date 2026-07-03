"""Period-Close bundler — pure logic for E3.

Generates the four QBO-importable CSVs for a given date range into a
single timestamped folder, alongside a README manifest and a
validation report. All Qt UI lives in
``jaks_inventory.ui.period_close_wizard``.

Outputs (in ``<output_dir>/qbo_export_<label>/``):

* ``invoices.csv``      — variant-aware, via :func:`qbo.csv_export.write_invoices_csv`
* ``bills.csv``         — via :func:`qbo.csv_export.write_bills_csv`
* ``payments.csv``      — via :func:`qbo.csv_export.write_payments_csv`
* ``credit_memos.csv``  — via :func:`qbo.csv_export.write_credit_memos_csv`
* ``README.txt``        — manifest + per-file import instructions
* ``validation_report.txt`` — output of :func:`qbo.validate.validate_for_export`
                              for each kind, joined.
"""
from __future__ import annotations

import calendar
import os
from dataclasses import dataclass, field
from datetime import date

from qbo.csv_export import (
    write_invoices_csv,
    write_bills_csv,
    write_payments_csv,
    write_credit_memos_csv,
)
from qbo.csv_variants import CsvVariant, native_support_note
from qbo.validate import validate_for_export, ValidationReport


# ───────────────────────── result dataclass ─────────────────────────

@dataclass
class PeriodCloseResult:
    folder: str
    files: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    validation_blocking: bool = False
    invoice_ids: list[int] = field(default_factory=list)
    po_ids: list[int] = field(default_factory=list)


# ───────────────────────── helpers ─────────────────────────

def month_range(year: int, month: int) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a calendar month."""
    last = calendar.monthrange(year, month)[1]
    return (
        date(year, month, 1).isoformat(),
        date(year, month, last).isoformat(),
    )


def folder_label(start: str, end: str) -> str:
    """Folder suffix: ``YYYY-MM`` for whole months, else ``start_to_end``."""
    if (
        len(start) >= 10 and len(end) >= 10
        and start[:7] == end[:7]
        and start.endswith("-01")
    ):
        # Likely whole-month range.
        return start[:7]
    return f"{start[:10]}_to_{end[:10]}"


def _safe_count(records, key="id") -> int:
    return sum(1 for r in records if r is not None)


def _hydrate_invoices(summaries: list[dict], get_invoice) -> list[dict]:
    if not get_invoice:
        return summaries
    out = []
    for s in summaries:
        try:
            full = get_invoice(int(s["id"])) or s
        except Exception:
            full = s
        out.append(full)
    return out


def _hydrate_pos(summaries: list[dict], get_po) -> list[dict]:
    if not get_po:
        return summaries
    out = []
    for s in summaries:
        try:
            full = get_po(int(s["id"])) or s
        except Exception:
            full = s
        out.append(full)
    return out


# ───────────────────────── manifest ─────────────────────────

_README_TEMPLATE = """\
QBO Period-Close Export
=======================
Period:           {start} → {end}
Generated:        {generated_at}
CSV variant:      {variant_label}

Files in this folder
--------------------
invoices.csv      ({inv_count} invoice(s), {inv_rows} line row(s))
bills.csv         ({bill_count} bill(s), {bill_rows} line row(s))
payments.csv      ({pay_rows} payment row(s))
credit_memos.csv  ({cred_rows} credit row(s))
validation_report.txt   pre-export sanity check (review before importing)

How to import
-------------
{native_note}

Suggested order:
  1. invoices.csv
  2. bills.csv
  3. payments.csv      (must come AFTER invoices)
  4. credit_memos.csv  (must come AFTER invoices if applied to one)

What was marked exported
------------------------
{export_marks}

Need to re-run?
---------------
Re-running this period after fixing data is safe — already-exported
records are excluded automatically. Manually-marked invoices will be
skipped on subsequent runs.
"""


# ───────────────────────── public entrypoint ─────────────────────────

def run_period_close(
    *,
    start_date: str,
    end_date: str,
    output_dir: str,
    variant: CsvVariant | str | None = CsvVariant.GENERIC,
    get_unexported_invoices,
    get_unexported_bills,
    get_paid_invoices_for_export,
    get_credit_memos_for_export,
    mark_invoices_exported,
    mark_pos_exported,
    log_qbo_export=None,
    get_invoice=None,
    get_purchase_order=None,
    tax_code_resolver=None,
    mark_exported: bool = True,
    generated_at: str | None = None,
) -> PeriodCloseResult:
    """Bundle all four CSVs for ``[start_date, end_date]`` into a new
    folder under ``output_dir``.

    All DB callables are injected so the bundler stays unit-testable
    without a live database. The QBO Export screen wires in the real
    ``db.*`` helpers.

    When ``mark_exported`` is True (default), invoices and POs included
    in the bundle are stamped via the supplied ``mark_*_exported``
    callables. Payments and credit memos have no mark step — they
    re-derive from invoice rows / the credit table.

    Returns a :class:`PeriodCloseResult` describing what was written.
    """
    label = folder_label(start_date, end_date)
    folder = os.path.join(output_dir, f"qbo_export_{label}")
    os.makedirs(folder, exist_ok=True)

    # Fetch ----------------------------------------------------------
    inv_summaries = get_unexported_invoices(start_date, end_date) or []
    bill_summaries = get_unexported_bills(start_date, end_date) or []
    payments = get_paid_invoices_for_export(start_date, end_date) or []
    credits = get_credit_memos_for_export(
        start_date=start_date, end_date=end_date) or []

    invoices = _hydrate_invoices(inv_summaries, get_invoice)
    pos = _hydrate_pos(bill_summaries, get_purchase_order)

    # Validate -------------------------------------------------------
    reports: dict[str, ValidationReport] = {
        "invoice": validate_for_export(invoices, "invoice"),
        "bill": validate_for_export(pos, "bill"),
        "payment": validate_for_export(payments, "payment"),
        "credit": validate_for_export(credits, "credit"),
    }
    blocking = any(r.is_blocking for r in reports.values())

    # Write CSVs -----------------------------------------------------
    paths = {
        "invoices": os.path.join(folder, "invoices.csv"),
        "bills": os.path.join(folder, "bills.csv"),
        "payments": os.path.join(folder, "payments.csv"),
        "credit_memos": os.path.join(folder, "credit_memos.csv"),
    }
    inv_rows = write_invoices_csv(
        paths["invoices"], invoices,
        tax_code_resolver=tax_code_resolver, variant=variant,
    )
    bill_rows = write_bills_csv(paths["bills"], pos, variant=variant)
    pay_rows = write_payments_csv(paths["payments"], payments, variant=variant)
    cred_rows = write_credit_memos_csv(
        paths["credit_memos"], credits, variant=variant)

    # Validation report ---------------------------------------------
    vr_path = os.path.join(folder, "validation_report.txt")
    with open(vr_path, "w", encoding="utf-8") as fh:
        for kind, r in reports.items():
            fh.write(f"=== {kind.upper()} ===\n")
            fh.write(r.as_text())
            fh.write("\n\n")
    paths["validation_report"] = vr_path

    # Mark exported (only if not blocking and caller wants it) -----
    invoice_ids = [int(s["id"]) for s in inv_summaries
                   if s.get("id") is not None]
    po_ids = [int(s["id"]) for s in bill_summaries
              if s.get("id") is not None]
    if mark_exported and not blocking:
        try:
            mark_invoices_exported(invoice_ids)
        except Exception:
            pass
        try:
            mark_pos_exported(po_ids)
        except Exception:
            pass
        export_marks = (
            f"{len(invoice_ids)} invoice(s) and {len(po_ids)} PO(s) "
            "marked qbo_exported=1."
        )
    elif blocking:
        export_marks = (
            "NOT MARKED — validation report contains errors. Fix the "
            "errors and re-run, or mark manually."
        )
    else:
        export_marks = "Skipped per caller request (mark_exported=False)."

    # Best-effort log to qbo_exports
    if log_qbo_export:
        try:
            log_qbo_export(
                "period_close", "csv",
                inv_rows + bill_rows + pay_rows + cred_rows,
                folder,
            )
        except Exception:
            pass

    # README ---------------------------------------------------------
    from datetime import datetime
    gen_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    variant_label = (
        variant.value if isinstance(variant, CsvVariant)
        else str(variant or "generic")
    )
    readme_path = os.path.join(folder, "README.txt")
    with open(readme_path, "w", encoding="utf-8") as fh:
        fh.write(_README_TEMPLATE.format(
            start=start_date,
            end=end_date,
            generated_at=gen_at,
            variant_label=variant_label,
            inv_count=len(invoices),
            inv_rows=inv_rows,
            bill_count=len(pos),
            bill_rows=bill_rows,
            pay_rows=pay_rows,
            cred_rows=cred_rows,
            native_note=native_support_note(variant),
            export_marks=export_marks,
        ))
    paths["readme"] = readme_path

    return PeriodCloseResult(
        folder=folder,
        files=paths,
        counts={
            "invoices": inv_rows,
            "bills": bill_rows,
            "payments": pay_rows,
            "credit_memos": cred_rows,
            "invoice_records": len(invoices),
            "bill_records": len(pos),
        },
        validation_blocking=blocking,
        invoice_ids=invoice_ids,
        po_ids=po_ids,
    )
