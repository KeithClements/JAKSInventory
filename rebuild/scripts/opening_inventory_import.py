"""
Opening inventory importer  (scripts/opening_inventory_import.py)
================================================================
One-time go-live loader that seeds real ON-HAND quantities AND cost basis for
the parts you physically stock, from a count-sheet CSV.

Unlike a bare quantity poke, every row is applied through ``InventoryService``
as a ledger-backed adjustment (reason = ``initial_inventory_load``) so:
  * the InventoryTransaction ledger + audit log record the load,
  * the nightly qty resync can't silently undo it, and
  * a supplied ``unit_cost`` sets the product's moving-average cost, so inventory
    valuation and margin are correct from day one. (A plain physical count sets
    quantity but NOT cost — this loader sets both.)

CSV columns (header row required; order-independent, case-insensitive):
    sku            required — JAKS SKU (falls back to vendor part # / vendor SKU)
    qty_on_hand    required — counted whole-unit quantity on the shelf
    unit_cost      optional — your unit cost; blank -> preferred vendor cost

Idempotent: the applied delta is ``(counted - current on-hand)``, so re-running
the same sheet is a no-op. Unknown SKUs are reported and NEVER created.

Dry-run by default (prints the plan, writes nothing). Pass ``--apply`` to commit.

    .venv\\Scripts\\python.exe -m scripts.opening_inventory_import data/opening_counts.csv
    .venv\\Scripts\\python.exe -m scripts.opening_inventory_import data/opening_counts.csv --apply
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

HEADER_ALIASES = {
    "sku":  {"sku", "jaks_sku", "jaks sku", "item", "part", "part_number", "part number"},
    "qty":  {"qty_on_hand", "qty", "quantity", "count", "counted", "on_hand", "onhand"},
    "cost": {"unit_cost", "cost", "unit cost", "avg_cost", "average_cost"},
}


def _map_columns(fieldnames):
    """Return {canonical: actual_header} for sku/qty/cost from the CSV header."""
    mapping: dict[str, str] = {}
    for actual in fieldnames or []:
        n = (actual or "").strip().lower()
        for canon, aliases in HEADER_ALIASES.items():
            if n in aliases:
                mapping.setdefault(canon, actual)
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser(description="Load opening on-hand quantities + cost from a count CSV")
    ap.add_argument("csv_path", help="path to the count sheet CSV (sku, qty_on_hand, unit_cost)")
    ap.add_argument("--db", default="data/jaks.db")
    ap.add_argument("--apply", action="store_true", help="commit (default is dry-run)")
    ap.add_argument("--user-id", type=int, default=None,
                    help="attribute the load to this user id (needs INVENTORY_ADJUST); "
                         "default: system event")
    ap.add_argument("--note", default="Opening inventory load (go-live)")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not os.path.exists(args.csv_path):
        print(f"FATAL: CSV not found: {args.csv_path}")
        return 2
    db_abs = os.path.abspath(args.db)
    if not os.path.exists(db_abs):
        print(f"FATAL: db not found: {db_abs}")
        return 2
    os.environ["JAKS_DB_PATH"] = db_abs  # MUST precede app.database import

    from app.database import SessionLocal, _apply_inline_migrations
    from app.models.product import Product, ProductVendorSource
    from app.services.inventory_service import InventoryService
    from app.constants import AdjustmentReason

    # Bring the target DB schema up to the code before ORM-loading Product
    # (idempotent ALTER ADD COLUMN; snapshots the file to backups/ first). The
    # app does this on startup; a standalone script must do it too.
    _apply_inline_migrations()

    db = SessionLocal()
    print(f"Target DB : {db_abs}")
    print(f"Run       : {'APPLY (will commit)' if args.apply else 'DRY-RUN (no writes)'}\n")

    with open(args.csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = _map_columns(reader.fieldnames)
        if "sku" not in cols or "qty" not in cols:
            print(f"FATAL: CSV needs a SKU column and a quantity column. "
                  f"Detected headers: {reader.fieldnames}")
            db.close()
            return 2
        rows = list(reader)

    def preferred_cost(product):
        srcs = db.query(ProductVendorSource).filter(
            ProductVendorSource.product_id == product.id).all()
        if not srcs:
            return None
        pref = next((s for s in srcs if getattr(s, "is_preferred", False)), srcs[0])
        vc = getattr(pref, "vendor_cost", None)
        try:
            return float(vc) if vc else None
        except (TypeError, ValueError):
            return None

    def resolve(ident: str):
        ident = (ident or "").strip()
        if not ident:
            return None, ""
        p = db.query(Product).filter(Product.sku == ident).first()
        if p:
            return p, "sku"
        pvs = (db.query(ProductVendorSource)
               .filter((ProductVendorSource.vendor_part_number == ident) |
                       (ProductVendorSource.vendor_sku == ident))
               .first())
        if pvs:
            p = db.query(Product).filter(Product.id == pvs.product_id).first()
            if p:
                return p, "vendor_part"
        return None, ""

    svc = InventoryService(db, current_user_id=args.user_id)

    planned = []          # (input_sku, resolved_sku, before, counted, delta, unit_cost, method)
    unchanged = 0
    not_found: list[str] = []
    bad_rows: list[tuple] = []

    for i, row in enumerate(rows, 2):  # row 1 is the header
        ident = (row.get(cols["sku"]) or "").strip()
        qty_raw = (row.get(cols["qty"]) or "").strip()
        cost_raw = (row.get(cols["cost"]) or "").strip() if cols.get("cost") else ""
        if not ident and not qty_raw:
            continue  # blank line
        try:
            fq = float(qty_raw)
        except (TypeError, ValueError):
            bad_rows.append((i, ident, f"qty not a number: {qty_raw!r}"))
            continue
        if fq != int(fq):
            bad_rows.append((i, ident, f"qty not a whole number: {qty_raw!r}"))
            continue
        counted = int(fq)
        if counted < 0:
            bad_rows.append((i, ident, f"negative qty: {counted}"))
            continue
        product, method = resolve(ident)
        if product is None:
            not_found.append(ident)
            continue
        before = product.qty_on_hand or 0
        delta = counted - before

        unit_cost = None
        if cost_raw:
            try:
                c = float(cost_raw)
                if c > 0:
                    unit_cost = c
            except (TypeError, ValueError):
                pass
        if unit_cost is None:
            unit_cost = preferred_cost(product)

        if delta == 0:
            unchanged += 1
            continue
        planned.append((ident, product.sku, before, counted, delta, unit_cost, method))

    applied = 0
    if args.apply:
        for ident, rsku, before, counted, delta, unit_cost, method in planned:
            product = db.query(Product).filter(Product.sku == rsku).first()
            svc.adjust_inventory(
                product_id=product.id,
                qty_delta=delta,
                reason=AdjustmentReason.INITIAL_INVENTORY_LOAD,
                note=f"{args.note} — counted {counted} (was {before})",
                unit_cost=(unit_cost if (delta > 0 and unit_cost) else None),
            )
            applied += 1

    total_units = sum(c for _, _, _, c, _, _, _ in planned)
    total_value = 0.0
    missing_cost = 0
    for _ident, _rsku, _before, counted, _delta, unit_cost, _method in planned:
        if unit_cost:
            total_value += counted * unit_cost
        else:
            missing_cost += 1

    print(f"rows in sheet        : {len(rows)}")
    print(f"to change (delta!=0) : {len(planned)}")
    print(f"already matching     : {unchanged}")
    print(f"unknown SKUs         : {len(not_found)}"
          + (f"  -> {not_found[:10]}{' ...' if len(not_found) > 10 else ''}" if not_found else ""))
    if bad_rows:
        print(f"bad rows             : {len(bad_rows)}  -> {bad_rows[:5]}")
    print(f"units to load        : {total_units}")
    print(f"inventory valuation  : ${total_value:,.2f}"
          + (f"   ({missing_cost} item(s) have NO cost -> valued at $0)" if missing_cost else ""))
    print()

    preview = planned[:25]
    if preview:
        print("first changes (sku | was -> counted | unit_cost | via):")
        for _ident, rsku, before, counted, _delta, unit_cost, method in preview:
            uc = f"${unit_cost:,.2f}" if unit_cost else "(none)"
            print(f"  {rsku:<24} {before:>5} -> {counted:<5}  {uc:>10}  {method}")
        if len(planned) > len(preview):
            print(f"  ... and {len(planned) - len(preview)} more")
    print()

    if args.apply:
        print(f"APPLIED {applied} adjustments (committed).")
    else:
        print("DRY-RUN complete — nothing written. Re-run with --apply to commit.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
