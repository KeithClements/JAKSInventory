"""QBO CSV export — pure functions that produce QBO-importable CSV files.

These writers emit ONE ROW PER LINE ITEM (the format QBO Online accepts on
its Invoice / Bill / Payment / Credit-Memo CSV imports). Header rows match
the column names used by the QBO Online file-import wizard (case-sensitive).

The module deliberately has no dependency on PySide6 or the database — UI
glue lives in ``jaks_inventory.ui.qbo_export_screen`` and DB fetch helpers
are passed in as already-shaped dicts. This keeps the writers unit-testable
without spinning up a Qt event loop or seeded database.

Schemas accepted
----------------
``invoice_dict`` (one per invoice, as returned by ``db.get_invoice``):
    ``invoice_number``, ``invoice_date``, ``due_date``, ``customer_name``,
    ``customer_company``, ``status``, ``subtotal``, ``tax_rate``,
    ``tax_amount``, ``shipping``, ``total``, ``notes``, and
    ``lines`` — list of ``{sku, description, quantity, unit_price,
    line_total, core_charge, product_title}``.

``po_dict`` (one per PO, as returned by ``db.get_purchase_order``):
    ``po_number``, ``order_date``, ``vendor_name``, ``status``, ``total``,
    ``notes``, and ``lines`` — list of ``{sku, title,
    quantity_ordered, quantity_received, unit_cost, line_total, notes}``.

``payment_dict`` (one per paid invoice — payments are aggregated on
``invoices.amount_paid``):
    ``invoice_number``, ``invoice_date``, ``customer_name``,
    ``customer_company``, ``amount_paid``, ``payment_method``.

``credit_dict`` (one per ``customer_credits`` row):
    ``id``, ``issue_date``, ``customer_name``, ``customer_company``,
    ``amount``, ``source_type``, ``status``, ``applied_invoice_number``,
    ``notes``.
"""
from __future__ import annotations

import csv
from typing import Callable, Iterable, Optional

from qbo.csv_variants import (
    CsvVariant,
    INVOICE_HEADERS,
    BILL_HEADERS,
    PAYMENT_HEADERS,
    CREDIT_HEADERS,
    get_headers,
)


CORE_CHARGE_SKU = "CORE-CHARGE"

# Default per-line tax marker when no jurisdictional code can be resolved.
# QBO Online accepts "TAX" / "NON" as generic codes during CSV import.
DEFAULT_TAX_CODE = "TAX"
DEFAULT_NON_TAX_CODE = "NON"


# Type alias for the optional tax-code lookup callback.
#   resolver(rate: float) -> str | None
# A return of None is treated as "use DEFAULT_TAX_CODE".
TaxCodeResolver = Callable[[float], Optional[str]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _customer_label(inv: dict) -> str:
    """QBO Customer column: prefer company, fall back to name."""
    return (
        (inv.get("customer_company") or "").strip()
        or (inv.get("customer_name") or "").strip()
        or "Unknown Customer"
    )


def _vendor_label(po: dict) -> str:
    return (po.get("vendor_name") or "").strip() or "Unknown Vendor"


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _date10(value) -> str:
    """Truncate timestamps to YYYY-MM-DD for QBO."""
    if value is None:
        return ""
    s = str(value)
    return s[:10] if len(s) >= 10 else s


# ---------------------------------------------------------------------------
# Invoice CSV — one row per line item
# ---------------------------------------------------------------------------

# Generic-variant header row, kept as a module constant for backward
# compatibility with tests and any external callers. Variant-aware
# headers live in :mod:`qbo.csv_variants`.
INVOICE_CSV_COLUMNS = list(INVOICE_HEADERS[CsvVariant.GENERIC])


def _expand_invoice_lines(invoice: dict) -> list[dict]:
    """Return invoice lines with a follow-on CORE-CHARGE row inserted after
    every parent line that carries a non-zero ``core_charge``. Pure
    re-implementation of ``qbo.core_charges.expand_invoice_lines_with_cores``
    that does not require a QBO connection.
    """
    out: list[dict] = []
    for ln in invoice.get("lines") or []:
        out.append(ln)
        core = _f(ln.get("core_charge"))
        qty = _f(ln.get("quantity") or ln.get("qty"))
        parent_sku = (ln.get("sku") or "").strip()
        if core > 0 and qty > 0:
            out.append({
                "sku": CORE_CHARGE_SKU,
                "description": f"Core deposit for {parent_sku or 'parent'}",
                "quantity": qty,
                "unit_price": core,
                "line_total": round(qty * core, 2),
                "core_charge": 0,
                "_is_core": True,
            })
    return out


def write_invoices_csv(
    path: str,
    invoices: Iterable[dict],
    tax_code_resolver: TaxCodeResolver | None = None,
    variant: CsvVariant | str | None = CsvVariant.GENERIC,
) -> int:
    """Write a QBO-Online-compatible Invoice CSV, ONE ROW PER LINE ITEM.

    For each invoice:
        * One row per ``invoice_lines`` entry.
        * If the line has ``core_charge > 0`` an extra row is emitted with
          item ``CORE-CHARGE``.
        * If ``shipping > 0`` an extra row is emitted with item
          ``SHIPPING`` (rate=shipping, qty=1).
        * When ``tax_amount > 0``, every taxable row gets a ``TaxCode``
          stamp resolved via ``tax_code_resolver(tax_rate)`` (or the
          generic ``"TAX"`` fallback). The aggregate ``TaxAmount`` is
          placed on the last row so QBO totals reconcile on import.
        * When ``tax_amount`` is zero, lines are stamped ``"NON"``.

    ``tax_code_resolver`` is optional and pure — pass
    ``db.get_qbo_tax_code_for_rate`` in production; tests can pass a
    lambda. A return of ``None`` from the resolver falls back to
    ``DEFAULT_TAX_CODE``.

    Returns the number of CSV rows written (excluding header).
    """
    rows_written = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(get_headers(variant, "invoice"))

        for inv in invoices:
            inv_no = inv.get("invoice_number", "")
            date = _date10(inv.get("invoice_date"))
            due = _date10(inv.get("due_date"))
            customer = _customer_label(inv)
            memo = (inv.get("notes") or "").replace("\n", " ").strip()
            tax_amount = _f(inv.get("tax_amount"))
            tax_rate = _f(inv.get("tax_rate"))
            shipping = _f(inv.get("shipping"))

            # Resolve the per-line TaxCode once per invoice.
            if tax_amount > 0:
                resolved: str | None = None
                if tax_code_resolver is not None:
                    try:
                        resolved = tax_code_resolver(tax_rate)
                    except Exception:
                        resolved = None
                line_tax_code = (resolved or DEFAULT_TAX_CODE).strip() or DEFAULT_TAX_CODE
            else:
                line_tax_code = DEFAULT_NON_TAX_CODE

            expanded = _expand_invoice_lines(inv)

            invoice_rows: list[list] = []
            for ln in expanded:
                sku = (ln.get("sku") or "").strip()
                desc = (
                    ln.get("description")
                    or ln.get("product_title")
                    or sku
                    or ""
                )
                qty = _f(ln.get("quantity") or ln.get("qty"))
                rate = _f(ln.get("unit_price"))
                amount = _f(ln.get("line_total")) or round(qty * rate, 2)
                # Discount lines (line_kind='discount') are non-taxable in QBO:
                # the discount reduces the taxable base by being applied
                # pre-tax via DiscountLineDetail semantics. We stamp them
                # explicitly as ``NON`` regardless of the invoice-level
                # tax code so QBO doesn't compute tax against the
                # negative amount.
                is_discount = (ln.get("line_kind") or "product") == "discount"
                row_tax_code = DEFAULT_NON_TAX_CODE if is_discount else line_tax_code
                invoice_rows.append([
                    inv_no, date, due, customer,
                    sku, desc, qty, f"{rate:.2f}", f"{amount:.2f}",
                    row_tax_code, "", memo,
                ])

            if shipping > 0:
                invoice_rows.append([
                    inv_no, date, due, customer,
                    "SHIPPING", "Shipping & handling", 1,
                    f"{shipping:.2f}", f"{shipping:.2f}",
                    line_tax_code, "", "",
                ])

            # Aggregate the invoice-level tax onto the final row's
            # TaxAmount column. QBO accepts a per-line aggregate for
            # totals reconciliation; we only stamp the amount, the code
            # is already on every row above.
            if invoice_rows and tax_amount > 0:
                invoice_rows[-1][10] = f"{tax_amount:.2f}"

            for row in invoice_rows:
                w.writerow(row)
                rows_written += 1

    return rows_written


# ---------------------------------------------------------------------------
# Bill CSV — one row per PO line
# ---------------------------------------------------------------------------

BILL_CSV_COLUMNS = list(BILL_HEADERS[CsvVariant.GENERIC])


def write_bills_csv(
    path: str,
    pos: Iterable[dict],
    variant: CsvVariant | str | None = CsvVariant.GENERIC,
) -> int:
    rows_written = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(get_headers(variant, "bill"))

        for po in pos:
            po_no = po.get("po_number", "")
            date = _date10(po.get("order_date"))
            vendor = _vendor_label(po)
            memo = (po.get("notes") or "").replace("\n", " ").strip()

            for ln in po.get("lines") or []:
                sku = (ln.get("sku") or "").strip()
                desc = (
                    ln.get("title")
                    or ln.get("description")
                    or sku
                    or ""
                )
                # Prefer received qty (what we actually got) but fall back
                # to ordered qty if not yet stamped.
                qty = _f(ln.get("quantity_received"))
                if qty <= 0:
                    qty = _f(ln.get("quantity_ordered"))
                cost = _f(ln.get("unit_cost"))
                amount = _f(ln.get("line_total")) or round(qty * cost, 2)
                w.writerow([
                    po_no, date, vendor,
                    sku, desc, qty, f"{cost:.2f}", f"{amount:.2f}",
                    memo,
                ])
                rows_written += 1

    return rows_written


# ---------------------------------------------------------------------------
# Customer-payment CSV — derived from invoices.amount_paid (no payment table)
# ---------------------------------------------------------------------------

PAYMENT_CSV_COLUMNS = list(PAYMENT_HEADERS[CsvVariant.GENERIC])


def write_payments_csv(
    path: str,
    payments: Iterable[dict],
    variant: CsvVariant | str | None = CsvVariant.GENERIC,
) -> int:
    """Write a QBO Receive-Payment CSV.

    Source rows are aggregated payment summaries derived from
    ``invoices.amount_paid``: one row per paid invoice. Payment number is
    synthesized as ``PMT-<invoice_number>`` so QBO treats re-imports as
    updates rather than duplicates.
    """
    rows_written = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(get_headers(variant, "payment"))

        for pay in payments:
            inv_no = pay.get("invoice_number", "")
            date = _date10(pay.get("invoice_date"))
            customer = _customer_label(pay)
            amount = _f(pay.get("amount_paid"))
            method = (pay.get("payment_method") or "").strip()
            if amount <= 0:
                continue
            w.writerow([
                f"PMT-{inv_no}",
                date,
                customer,
                inv_no,
                f"{amount:.2f}",
                method,
                "",
            ])
            rows_written += 1

    return rows_written


# ---------------------------------------------------------------------------
# Credit-memo CSV — sourced from customer_credits table
# ---------------------------------------------------------------------------

CREDIT_MEMO_CSV_COLUMNS = list(CREDIT_HEADERS[CsvVariant.GENERIC])

# Stable per-source SKUs so the same credit re-imports as the same memo.
_CREDIT_ITEM_BY_SOURCE = {
    "core": ("CORE-CREDIT", "Core return credit"),
    "manual": ("CREDIT", "Customer credit"),
    "return": ("RETURN-CREDIT", "Return credit"),
}


def write_credit_memos_csv(
    path: str,
    credits: Iterable[dict],
    variant: CsvVariant | str | None = CsvVariant.GENERIC,
) -> int:
    rows_written = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(get_headers(variant, "credit"))

        for c in credits:
            credit_id = c.get("id") or ""
            date = _date10(c.get("issue_date"))
            customer = _customer_label(c)
            amount = _f(c.get("amount"))
            source = (c.get("source_type") or "manual").lower()
            sku, default_desc = _CREDIT_ITEM_BY_SOURCE.get(
                source, ("CREDIT", "Customer credit")
            )
            desc = (c.get("notes") or "").replace("\n", " ").strip() or default_desc
            status = (c.get("status") or "open").lower()
            applied_inv = c.get("applied_invoice_number") or ""
            w.writerow([
                f"CM-{credit_id}",
                date,
                customer,
                sku,
                desc,
                1,
                f"{amount:.2f}",
                f"{amount:.2f}",
                status,
                applied_inv,
                "",
            ])
            rows_written += 1

    return rows_written
