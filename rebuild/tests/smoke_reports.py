"""
Smoke checks for Reports & Analytics Series 1.
Run: py -3.12 -X utf8 tests/smoke_reports.py

Verifies the five Phase 1D acceptance items against the live SQLite DB. Output
is plain ASCII pass/fail so it works on Windows consoles.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Allow running this script directly from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.services.report_service import ReportService
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.constants import InvoiceStatus, CoreStatus


def _mark(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def main() -> int:
    db = SessionLocal()
    svc = ReportService(db)
    failures = 0

    print("=== Reports & Analytics Series 1 — smoke checks ===")

    # [1] AR aging excludes voided invoices
    voided_count = (
        db.query(Invoice).filter(Invoice.status == InvoiceStatus.VOID).count()
    )
    ar = svc.get_ar_aging()
    ar_invoice_ids = {inv.id for row in ar["rows"] for inv in row["invoices"]}
    if ar_invoice_ids:
        voided_in_aging = (
            db.query(Invoice)
            .filter(
                Invoice.id.in_(ar_invoice_ids),
                Invoice.status == InvoiceStatus.VOID,
            )
            .count()
        )
    else:
        voided_in_aging = 0
    ok1 = voided_in_aging == 0
    failures += 0 if ok1 else 1
    print(
        f"[1] AR aging excludes voided invoices: "
        f"voided_in_db={voided_count}, voided_in_report={voided_in_aging} "
        f"-> {_mark(ok1)}"
    )

    # [2] Inventory valuation total = sum(qty_on_hand * cost) for in-stock SKUs
    inv = svc.get_inventory_valuation()
    expected_value = round(
        sum(
            p.qty_on_hand * (p.cost or 0.0)
            for p in db.query(Product).filter(Product.is_active.is_(True)).all()
            if p.qty_on_hand > 0
        ),
        2,
    )
    reported_value = inv["totals"]["total_value"]
    ok2 = abs(reported_value - expected_value) < 0.01
    failures += 0 if ok2 else 1
    print(
        f"[2] Inventory total math: "
        f"reported=${reported_value:.2f}, expected=${expected_value:.2f} "
        f"-> {_mark(ok2)}"
    )

    # [3] Sales by product handles missing product_id without crashing
    null_lines = (
        db.query(InvoiceLine).filter(InvoiceLine.product_id.is_(None)).count()
    )
    try:
        sbp = svc.get_sales_by_product(date(2020, 1, 1), date.today())
        ok3 = True
        none_bucket = any(r["product"] is None for r in sbp["rows"])
    except Exception as exc:  # noqa: BLE001
        ok3 = False
        none_bucket = False
        print(f"    crashed: {exc}")
    failures += 0 if ok3 else 1
    print(
        f"[3] Sales by product handles NULL product_id: "
        f"null_lines_in_db={null_lines}, none_bucket_present={none_bucket} "
        f"-> {_mark(ok3)}"
    )

    # [4] Open PO remaining = ordered - received - cancelled
    pos = svc.get_open_pos()
    mismatches = []
    line_count = 0
    for row in pos["rows"]:
        for ln in row["lines"]:
            line_count += 1
            expected_rem = max(
                ln["qty_ordered"] - ln["qty_received"] - ln["qty_cancelled"], 0
            )
            if ln["qty_remaining"] != expected_rem:
                mismatches.append(
                    (row["po_number"], ln["sku"], ln["qty_remaining"], expected_rem)
                )
    ok4 = not mismatches
    failures += 0 if ok4 else 1
    print(
        f"[4] Open PO remaining formula: "
        f"lines_checked={line_count}, mismatches={len(mismatches)} "
        f"-> {_mark(ok4)}"
    )
    for m in mismatches[:3]:
        print(f"     - {m}")

    # [5] Core report shows only open/partial cores
    cores = svc.get_core_charges_outstanding()
    bad_statuses = [
        r for r in cores["rows"]
        if r["core"].status not in (CoreStatus.OPEN, CoreStatus.PARTIAL)
    ]
    ok5 = not bad_statuses
    failures += 0 if ok5 else 1
    print(
        f"[5] Core report only open/partial: "
        f"rows={len(cores['rows'])}, non_open={len(bad_statuses)} "
        f"-> {_mark(ok5)}"
    )

    # Diagnostic: AR totals + sales-by-customer snapshot
    sbc = svc.get_sales_by_customer(date(2020, 1, 1), date.today())
    print(
        f"[snapshot] AR total=${ar['totals']['total']:.2f} | "
        f"sales rows={len(sbc['rows'])} "
        f"gross=${sbc['totals']['gross_sales']:.2f} "
        f"margin=${sbc['totals']['margin']:.2f}"
    )

    db.close()
    print(f"=== {failures} failure(s) ===")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
