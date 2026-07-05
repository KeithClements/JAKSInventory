"""
scripts/load_physical_count.py
==============================
Load a physical inventory count into the ERP — the go-live gating task
(FULL_ERP_REVIEW_2026-07-04 / QBO_SANDBOX_GO_LIVE_PLAN §4c). Sets each product's
on-hand to the counted quantity via a ledger-backed adjustment, so the load is
auditable and the nightly cache-vs-ledger resync can't undo it.

Input: a CSV with a SKU column and a quantity column. Header names are flexible —
  sku / SKU / part / part_number   and   qty / count / counted_qty / on_hand
Rows with unknown SKUs are reported and skipped (never created).

Usage (from the rebuild folder, with the venv):
  # Preview only (default) — shows exactly what WOULD change, writes nothing:
  .venv\\Scripts\\python.exe scripts\\load_physical_count.py counts.csv

  # Apply for real:
  .venv\\Scripts\\python.exe scripts\\load_physical_count.py counts.csv --apply

  # Against the sandbox trial DB:
  set JAKS_DB_PATH=data\\jaks_sandbox.db
  .venv\\Scripts\\python.exe scripts\\load_physical_count.py counts.csv --apply

ALWAYS take a backup first (python -c "from app.services.backup_service import
create_backup; print(create_backup())") — this changes on-hand for every counted SKU.
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_SKU_KEYS = ("sku", "part", "part_number", "part_no", "partnumber")
_QTY_KEYS = ("qty", "count", "counted_qty", "counted", "on_hand", "quantity")


def _norm(header: str) -> str:
    """Normalize a CSV header for matching: lowercase, spaces/hyphens → underscore
    (so 'Part Number' and 'On Hand' match 'part_number' / 'on_hand')."""
    return (header or "").strip().lower().replace(" ", "_").replace("-", "_")


def _pick(row: dict, keys: tuple[str, ...]) -> str | None:
    lower = {_norm(k): v for k, v in row.items()}
    for k in keys:
        if k in lower and str(lower[k]).strip() != "":
            return str(lower[k]).strip()
    return None


def read_counts(path: str) -> list[tuple[str, str]]:
    """Parse (sku, qty) pairs from the CSV; raises on a missing column."""
    out: list[tuple[str, str]] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            sku = _pick(row, _SKU_KEYS)
            qty = _pick(row, _QTY_KEYS)
            if sku is None or qty is None:
                raise ValueError(
                    f"Row {i}: could not find a SKU column ({_SKU_KEYS}) and a "
                    f"quantity column ({_QTY_KEYS}). Got headers: {list(row.keys())}"
                )
            out.append((sku, qty))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Load a physical count into on-hand.")
    ap.add_argument("csv_path", help="CSV with SKU + quantity columns")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry-run preview)")
    ap.add_argument("--note", default="Physical count load",
                    help="note stamped on each inventory adjustment ledger row")
    args = ap.parse_args(argv)

    counts = read_counts(args.csv_path)
    print(f"Read {len(counts)} row(s) from {args.csv_path}")

    from app.database import SessionLocal
    from app.services.inventory_service import InventoryService

    db = SessionLocal()
    try:
        # current_user_id=None → system actor (script has no session).
        svc = InventoryService(db, current_user_id=None)
        summary = svc.apply_physical_count(counts, note=args.note, dry_run=not args.apply)
    finally:
        db.close()

    mode = "APPLIED" if args.apply else "DRY-RUN (nothing written)"
    print(f"\n=== {mode} ===")
    print(f"  would-change / changed : {summary['applied']}")
    print(f"  already correct        : {summary['unchanged']}")
    print(f"  unknown SKUs (skipped) : {len(summary['not_found'])}")
    if summary["not_found"]:
        print("    " + ", ".join(summary["not_found"][:20])
              + (" ..." if len(summary["not_found"]) > 20 else ""))
    for c in summary["changes"][:50]:
        print(f"    {c['sku']:<24} {c['before']:>6} -> {c['after']:<6} ({c['delta']:+d})")
    if len(summary["changes"]) > 50:
        print(f"    ... and {len(summary['changes']) - 50} more")
    if not args.apply and summary["changes"]:
        print("\nRe-run with --apply to write these changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
