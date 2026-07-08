"""
app/invoice_totals.py
======================
THE one invoice-totals engine.

Both the Invoice model's money properties (``subtotal`` / ``discount_amount`` /
``tax_amount`` / ``total`` / ``balance_due`` — read by the List "Total" column,
the row Preview panel, and the print/PDF) and ``InvoiceService.calculate_totals``
(the workspace totals panel) delegate to :func:`compute_invoice_totals`, so the
customer-facing surfaces and the workspace can never disagree.

Policy locked in here (was split across two drifting engines before)
--------------------------------------------------------------------
* **Cores are never taxed.** A core charge is a refundable DEPOSIT, not a sale
  (``app.constants.NON_TAXABLE_LINE_TYPES``). The customer still pays the deposit,
  so it counts toward the grand total, but it sits in its own subtotal and is
  excluded from the taxable base. (Before: the model taxed the entire subtotal,
  cores included, while the service excluded them — so a taxable invoice with a
  core charge showed a different total in the workspace vs the List/print.)
* **Every line's ``line_total`` counts toward TOTAL** — including fee / credit
  lines (MISC_FEE, RESTOCKING_FEE, NSF_FEE, WARRANTY_CREDIT, DISCOUNT,
  CC_SURCHARGE, TAX). The old workspace engine bucketed only parts / cores /
  freight and *silently dropped* everything else, so e.g. an NSF-fee invoice
  (``PaymentService.process_nsf`` creates one whose only line is an NSF_FEE)
  rendered ``$0.00`` in the workspace while the List/print showed the real fee.
  Now ``total = subtotal − discount + tax`` where ``subtotal`` sums *every*
  ``line_total`` — provably equal to
  ``parts_after_discount + core + freight + other + tax``.
* **Invoice-level discount** (``invoice.discount_pct``) applies ONCE to the PARTS
  subtotal only (PRODUCT / MISC / WARRANTY). Cores and freight are not discounted.
* **Tax.** Once finalized, each line carries a frozen ``tax_amount`` (snapshotted
  in ``InvoiceService.finalise``) which is the source of truth. For drafts, tax is
  computed live from the invoice-level rate on the discounted-parts + freight base.
  Per-line ``is_taxable`` governs the frozen path; cores/fees default non-taxable
  and so drop out of tax in both paths.

Pure — no DB access. Takes any object exposing ``.lines`` (each with
``.line_total`` / ``.line_type`` / ``.unit_price`` / ``.qty`` / ``.discount_pct``
/ ``.is_taxable`` / ``.tax_amount``), plus ``.discount_pct`` / ``.is_taxable`` /
``.tax_rate`` / ``.tax_rate_snapshot`` / ``.tax_exempt_snapshot`` /
``.cc_surcharge_pct`` / ``.apply_cc_surcharge`` / ``.amount_paid``. Imports only
``app.constants`` (a pure-enum leaf), so it is safe to import from both
``app.models`` and ``app.services`` without a circular dependency.
"""
from __future__ import annotations

from app.constants import FREIGHT_LINE_TYPES, InvoiceStatus, LineType, PARTS_LINE_TYPES


def compute_invoice_totals(invoice) -> dict:
    """Return the full money breakdown for ``invoice``. See module docstring."""
    # ── Bucket every line by economic role ──────────────────────────────────
    parts_subtotal = 0.0
    core_subtotal = 0.0
    freight_subtotal = 0.0
    other_subtotal = 0.0      # fees & credits — counted toward TOTAL, not dropped
    for ln in invoice.lines:
        amount = ln.line_total
        if ln.line_type in PARTS_LINE_TYPES:
            parts_subtotal += amount
        elif ln.line_type == LineType.CORE_CHARGE:
            core_subtotal += amount
        elif ln.line_type in FREIGHT_LINE_TYPES:
            freight_subtotal += amount
        else:
            other_subtotal += amount

    parts_subtotal = round(parts_subtotal, 2)
    core_subtotal = round(core_subtotal, 2)
    freight_subtotal = round(freight_subtotal, 2)
    other_subtotal = round(other_subtotal, 2)
    subtotal = round(parts_subtotal + core_subtotal + freight_subtotal + other_subtotal, 2)

    # ── Invoice-level discount: parts only (cores & freight are pass-through) ─
    discount_pct = invoice.discount_pct or 0.0
    discount_amount = round(parts_subtotal * (discount_pct / 100), 2) if discount_pct else 0.0
    parts_after_discount = round(parts_subtotal - discount_amount, 2)

    # ── Tax ─────────────────────────────────────────────────────────────────
    # Prefer the per-line tax_amount frozen at finalize; fall back to a live
    # invoice-level calc for drafts that haven't been finalized yet.
    sum_line_tax = round(sum((ln.tax_amount or 0.0) for ln in invoice.lines), 2)
    tax_rate_display = invoice.tax_rate_snapshot or invoice.tax_rate
    if sum_line_tax > 0:
        # Frozen path — snapshotted per-line tax_amounts are the source of truth.
        is_taxable_display = not invoice.tax_exempt_snapshot
        taxable_amount = round(
            sum(
                ln.unit_price * ln.qty * (1 - (ln.discount_pct or 0) / 100)
                for ln in invoice.lines
                if ln.is_taxable
            ),
            2,
        )
        tax_amount = sum_line_tax
    else:
        # Draft / legacy fallback — live calc on (discounted parts + freight).
        # Cores are excluded (refundable deposit, never taxed).
        #
        # D-1: invoice-level is_taxable is the AUTHORITATIVE gate. It is reliably
        # set at draft creation (InvoiceService.create_draft / create_invoice from
        # the customer's is_tax_exempt + tax_rate), so a False value is a real
        # signal — either a tax-exempt account or the user unchecking "Taxable" on
        # the workspace — and must zero tax everywhere. The old
        # `or tax_rate_display > 0` fallback re-enabled tax whenever a rate snapshot
        # existed, which silently defeated an explicit uncheck. Re-checking
        # "Taxable" sets is_taxable back to True and the retained tax_rate_snapshot
        # restores the rate.
        is_taxable_display = (not invoice.tax_exempt_snapshot) and bool(invoice.is_taxable)
        taxable_amount = (
            round(parts_after_discount + freight_subtotal, 2)
            if is_taxable_display
            else 0.0
        )
        tax_amount = (
            round(taxable_amount * (tax_rate_display / 100), 2)
            if is_taxable_display
            else 0.0
        )

    # ── Grand total — every line counts ─────────────────────────────────────
    total = round(subtotal - discount_amount + tax_amount, 2)

    # R1 — CC surcharge is applied AT PAYMENT TIME on the card portion only; this
    # is an INFORMATIONAL estimate ("if the whole balance is paid by card…") and
    # is NOT added to the total.
    cc_pct = invoice.cc_surcharge_pct or 0.0
    cc_surcharge_estimate = (
        round(total * (cc_pct / 100), 2)
        if (invoice.apply_cc_surcharge or cc_pct > 0)
        else 0.0
    )

    amount_paid = invoice.amount_paid

    # A voided invoice has no balance due — it is cancelled. Zeroing here
    # (rather than in every caller) keeps every surface consistent: the
    # workspace, the list column, the print/PDF, and the AR aging all read
    # balance_due through this one engine. The original total/subtotal are
    # preserved so the workspace can still display the historical amounts.
    if getattr(invoice, "status", None) == InvoiceStatus.VOID:
        amount_paid = round(total, 2)   # treat entire total as "settled" by the void

    return {
        "subtotal":              subtotal,
        "parts_subtotal":        parts_subtotal,
        "core_subtotal":         core_subtotal,
        "freight_subtotal":      freight_subtotal,
        "other_subtotal":        other_subtotal,
        "discount_amount":       discount_amount,
        "taxable_amount":        taxable_amount,
        "tax_rate_display":      tax_rate_display,
        "is_taxable_display":    is_taxable_display,
        "tax_amount":            tax_amount,
        # Informational only — surcharge isn't added to total. Templates can show
        # "If paid by card: total + estimated surcharge = $X.XX".
        "cc_fee_amount":         cc_surcharge_estimate,
        "cc_surcharge_estimate": cc_surcharge_estimate,
        "total":                 total,
        "amount_paid":           amount_paid,
        "balance_due":           round(total - amount_paid, 2),
    }
