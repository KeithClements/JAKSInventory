"""
QuickBooks Products/Services → Axle ERP importer  (scripts/qbo_products_import.py)
==================================================================================
One-time sync of the owner's QuickBooks "Products & Services" export into the ERP
WITHOUT creating duplicate SKUs. Owner-locked decisions (2026-07-07):

  1. NEW items keep their QuickBooks SKU as-is (house-brand style) — no vendor-digit
     minting. Malformed / name-only SKUs are FLAGGED for review, never guessed.
  2. Existing ERP products are LEFT UNTOUCHED (identity/price/category/description);
     the importer only makes sure a second copy isn't created.
  3. The 69 rows carrying an on-hand quantity ALSO get a ledger-backed opening
     inventory load (qty + moving-avg cost), exactly like opening_inventory_import.
  4. The 41 "G2 reman injector" rows (SKU buried in the name) ARE imported; the
     legacy/duplicate/bookkeeping junk is SKIPPED and reported.

Dedup is layered so the CSV's own messiness can't leak duplicates into the ERP:
  • exact product.sku (case-insensitive)               → EXISTING (skip create)
  • normalized product.sku (separators stripped)       → EXISTING
  • normalized vendor part# / vendor_sku (single hit)  → EXISTING
  • in-batch: same exact SKU already queued            → in-file dup (skip)
  • in-batch: same normalized bare part# already made  → in-file part dup (skip)
  • otherwise                                          → CREATE (keep QBO SKU)

Dry-run by default (writes nothing, prints the full plan + a per-row decision CSV).
Pass --apply to commit; --apply snapshots the DB to backups/ first.

    .venv\\Scripts\\python.exe -m scripts.qbo_products_import
    .venv\\Scripts\\python.exe -m scripts.qbo_products_import --apply --confirm "IMPORT QBO PRODUCTS"
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime

DEFAULT_CSV = (r"C:\Users\keith\OneDrive\Desktop"
               r"\ProductsServicesList_JAK&#x27;s_Diesel,_LLC_7_7_2026.csv")

# Vendor-code tokens we recognize as the middle segment of a JAKS-<CODE>-<part>
# SKU. Used ONLY to recover the bare part number for dedup matching — the created
# SKU always keeps the full QuickBooks string.
_VENDOR_CODES = {
    "PAI", "IMB", "SMP", "SAMPA", "DFT", "MIG", "MGO", "CTT", "CTP", "CTR",
    "HHP", "FTP", "STS", "CAP", "LVP", "MCB", "G2",
}

# Rows that are QuickBooks bookkeeping constructs, not physical parts. Matched on
# the NON-inventory rows only (an Item type of "Inventory" always means a real
# part, however oddly it was booked).
_BOOKKEEPING_NAMES = {
    "fuel surcharge", "warranty", "late fee", "core charge - customer core deposits",
    "shipping", "credit card fee", "credit card fee (from vendor)", "custom amount",
    "services", "hours", "freight / shipping", "vendor core charge",
}
_BOOKKEEPING_INCOME = {
    "services", "shopify sales", "shopify shipping income", "shopify tips",
    "shopify discount", "late fee income", "customer core deposits",
    "quickbooks payments sales", "shopify sales tax", "shopify refund adjustment",
    "shopify sales tax payable - usd", "shopify gift card - usd",
}
# Names that are clearly a scratch marker, not a product, even when Item type is
# Inventory (the QuickBooks legacy tail).
_JUNK_NAMES = {"pai", "core charge", "injector core", "pai - core", "core",
               "inframe", "inframee", "services"}
_JUNK_SKUS = {"pai", "core", "core charge", "injector core", "inframe", "inframee"}

# QuickBooks Category (deepest segment, lowercased) → ERP product_categories.id.
# Best-effort; anything unmapped is created uncategorized and reported.
_CATEGORY_ALIASES = {
    "injectors": 17, "injector": 17,
    "cylinder heads": 21, "cylinder head": 21,
    "suspension": 30, "airbag": 30, "airbags": 30, "shocks": 30,
    "gasket kits": 28, "head gasket set": 28, "gaskets": 22,
    "waterpump": 29, "water pump": 29, "water pumps": 29,
    "oil pump": 26, "oil pumps": 26,
    "headbolts": 7, "head bolts": 7,
    "inframes": 31, "inframe kits": 31, "engine rebuild kits": 31,
    "turbo": 4, "turbos": 8, "turbocharger": 4,
    "sensor": 20, "sensors": 20, "emissions": 20,
    "clutches": 23, "clutch": 23,
    "king pins": 33, "steering": 33,
    "manifold": 12, "manifolds": 12,
    "liners": 13, "pistons": 16, "bearings": 9,
}

_SEPS = ("-", " ", ".", "/", "_", "(", ")", "+", "#", "%")


def _norm(s: str) -> str:
    out = (s or "").lower()
    for sep in _SEPS:
        out = out.replace(sep, "")
    return out


def _to_float(s):
    try:
        return round(float(str(s).replace(",", "").replace("$", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def _bare_part(sku: str) -> str:
    """'JAKS-PAI-340632-010' -> '340632-010'; 'JAKS-10R8501' -> '10R8501';
    'JAKS=ISX118-145' -> 'ISX118-145'. Strips a leading JAKS-/JAKS= and, only when
    the next segment is a known vendor code, that segment too."""
    s = (sku or "").strip()
    m = re.match(r"^JAKS[-=](.+)$", s, re.I)
    if not m:
        return s
    rest = m.group(1)
    seg = re.match(r"^([A-Za-z0-9]{2,6})[-\s](.+)$", rest)
    if seg and seg.group(1).upper() in _VENDOR_CODES:
        return seg.group(2)
    return rest


def _sku_unusable_reason(sku: str, name: str) -> str | None:
    """Return a reason string when a NEW-candidate SKU can't be used verbatim
    (so the row is flagged for the owner, not guessed), else None."""
    s = (sku or "").strip()
    if not s:
        return "blank SKU"
    if s.lower() in _JUNK_SKUS or name.strip().lower() in _JUNK_NAMES:
        return "scratch/marker row"
    if s.strip().lower() == name.strip().lower():
        return "SKU is the product name"
    if "," in s:
        return "SKU contains a comma (looks like a description)"
    if re.search(r"-\s", s):
        return "malformed SKU (space after a dash)"
    if " " in s and not s.upper().startswith("JAKS"):
        return "SKU has spaces and no JAKS prefix (looks like a description)"
    return None


def _map_category(qbo_cat: str, title: str, byname: dict) -> tuple[int | None, bool]:
    """(category_id, matched?). Try exact ERP name, then deepest ':' segment,
    then the alias table, then a keyword sniff of the title."""
    c = (qbo_cat or "").strip()
    if c:
        if c.lower() in byname:
            return byname[c.lower()], True
        deepest = c.split(":")[-1].strip().lower()
        if deepest in byname:
            return byname[deepest], True
        if deepest in _CATEGORY_ALIASES:
            return _CATEGORY_ALIASES[deepest], True
    t = (title or "").lower()
    for kw, cid in (("injector", 17), ("cylinder head", 21), ("head gasket", 28),
                    ("gasket", 22), ("water pump", 29), ("oil pump", 26),
                    ("head bolt", 7), ("headbolt", 7), ("camshaft", 11),
                    ("turbo", 4), ("inframe", 31), ("air spring", 30),
                    ("air bag", 30), ("airbag", 30), ("king pin", 33),
                    ("fifth wheel", 30), ("manifold", 12), ("sensor", 20)):
        if kw in t:
            return cid, True
    return None, False


def main() -> int:
    ap = argparse.ArgumentParser(description="Import the QuickBooks products list into the ERP without duplicating SKUs")
    ap.add_argument("csv_path", nargs="?", default=DEFAULT_CSV)
    ap.add_argument("--db", default="data/jaks.db")
    ap.add_argument("--apply", action="store_true", help="commit (default: dry-run)")
    ap.add_argument("--confirm", default="", help='required on --apply: "IMPORT QBO PRODUCTS"')
    ap.add_argument("--report", default=None, help="path for the per-row decision CSV")
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
    if args.apply and args.confirm != "IMPORT QBO PRODUCTS":
        print('FATAL: --apply requires --confirm "IMPORT QBO PRODUCTS"')
        return 2

    os.environ["JAKS_DB_PATH"] = db_abs
    from app.database import SessionLocal, _apply_inline_migrations
    from app.models.product import Product, ProductCategory, ProductVendorSource
    from app.services.product_service import ProductService
    from app.services.inventory_service import InventoryService
    from app.constants import AdjustmentReason
    _apply_inline_migrations()

    if args.apply:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bdir = os.path.join(os.path.dirname(db_abs), "backups")
        os.makedirs(bdir, exist_ok=True)
        bpath = os.path.join(bdir, f"jaks-pre-qbo-products-{ts}.db")
        shutil.copy2(db_abs, bpath)
        print(f"Backup    : {bpath}")

    db = SessionLocal()
    print(f"Target DB : {db_abs}")
    print(f"Run       : {'APPLY (will commit)' if args.apply else 'DRY-RUN (no writes)'}")
    print(f"Source    : {args.csv_path}\n")

    # ── ERP indexes ──────────────────────────────────────────────────────────
    sku_exact: dict[str, tuple[int, str]] = {}
    sku_norm_map: dict[str, list[tuple[int, str]]] = defaultdict(list)
    pid_to_sku: dict[int, str] = {}
    for pid, sku, snorm in db.query(Product.id, Product.sku, Product.sku_norm):
        if sku:
            sku_exact[sku.strip().upper()] = (pid, sku)
            pid_to_sku[pid] = sku
        if snorm:
            sku_norm_map[snorm].append((pid, sku))
    part_norm_map: dict[str, set[int]] = defaultdict(set)
    for pid, vpnn, vskun in db.query(ProductVendorSource.product_id,
                                     ProductVendorSource.vendor_part_number_norm,
                                     ProductVendorSource.vendor_sku_norm):
        if vpnn:
            part_norm_map[vpnn].add(pid)
        if vskun:
            part_norm_map[vskun].add(pid)
    cat_byname = {c.name.strip().lower(): c.id
                  for c in db.query(ProductCategory).filter(ProductCategory.is_active == True)}  # noqa: E712

    def resolve_existing(sku: str) -> tuple[int, str] | None:
        u = sku.strip().upper()
        if u in sku_exact:
            return sku_exact[u]
        n = _norm(sku)
        if n and n in sku_norm_map and len({p for p, _ in sku_norm_map[n]}) == 1:
            return sku_norm_map[n][0]
        bp = _norm(_bare_part(sku))
        if len(bp) >= 4 and bp in part_norm_map and len(part_norm_map[bp]) == 1:
            pid = next(iter(part_norm_map[bp]))
            return (pid, pid_to_sku.get(pid, sku))
        return None

    # ── Parse + classify ─────────────────────────────────────────────────────
    with open(args.csv_path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    to_create: list[dict] = []      # {sku,title,desc,cat_id,price,cost,qty,line,pref_vendor,cat_matched}
    existing_hits: list[dict] = []  # {line,qbo_sku,erp_pid,erp_sku,qty,cost}
    flagged: list[tuple] = []       # (line, name, sku, reason)
    bookkeeping: list[tuple] = []   # (line, name, itype)
    in_file_dup: list[tuple] = []   # (line, sku, reason)
    price_review: list[tuple] = []  # (line, sku, price, cost, note)

    batch_exact: set[str] = set()
    batch_part: dict[str, str] = {}  # part_norm -> first sku that claimed it

    for i, r in enumerate(rows, 2):
        name = (r.get("Product/Service Name") or "").strip()
        itype = (r.get("Item type") or "").strip()
        cat = (r.get("Category") or "").strip()
        sku = (r.get("SKU") or "").strip()
        income = (r.get("Income Account") or "").strip().lower()
        qty_raw = (r.get("Quantity on hand") or "").strip()
        price = _to_float(r.get("Price"))
        cost = _to_float(r.get("Cost"))
        title = name

        # G2 reman injectors: SKU is buried in the name "JAKS-G2-XXX | Title".
        if not sku and "|" in name and name.upper().startswith("JAKS-G2"):
            left, _, right = name.partition("|")
            sku = left.strip()
            title = right.strip() or left.strip()

        try:
            qf = float(qty_raw) if qty_raw else 0.0
        except ValueError:
            qf = 0.0
        counted = int(qf) if qf == int(qf) else 0

        # Bookkeeping / service constructs (non-inventory only).
        if itype != "Inventory":
            if (itype == "Service"
                    or name.lower().startswith("shopify")
                    or name.lower() in _BOOKKEEPING_NAMES
                    or income in _BOOKKEEPING_INCOME):
                bookkeeping.append((i, name, itype))
                continue

        # Dedup vs existing ERP product (leave those untouched).
        hit = resolve_existing(sku) if sku else None
        if hit:
            existing_hits.append({"line": i, "qbo_sku": sku, "erp_pid": hit[0],
                                  "erp_sku": hit[1], "qty": counted, "cost": cost})
            continue

        # New candidate — must have a usable SKU.
        reason = _sku_unusable_reason(sku, name)
        if reason:
            flagged.append((i, name, sku, reason))
            continue

        # In-batch dedup (exact + bare part).
        up = sku.strip().upper()
        if up in batch_exact:
            in_file_dup.append((i, sku, "duplicate SKU already queued this run"))
            continue
        bp = _norm(_bare_part(sku))
        if len(bp) >= 4 and bp in batch_part:
            in_file_dup.append((i, sku, f"same part# as earlier row {batch_part[bp]!r}"))
            continue
        batch_exact.add(up)
        if len(bp) >= 4:
            batch_part[bp] = sku

        # Price sanity: a sell price wildly out of line with cost is almost always
        # a QuickBooks typo (e.g. 6920.86 for 692.86). Don't push a mispriced
        # product live — create it WITHOUT a sell price (it then prices off
        # cost×markup) and list it for the owner to set the real price.
        price_note = ""
        if price is not None and cost and cost > 0:
            if price > cost * 8:
                price_note = f"price ${price:,.2f} is {price/cost:.0f}× cost ${cost:,.2f} (likely typo)"
            elif price < cost:
                price_note = f"price ${price:,.2f} below cost ${cost:,.2f} (negative margin)"
        if price_note:
            price_review.append((i, sku, price, cost, price_note))
            price = None  # withhold the suspicious price; owner sets it

        cat_id, cat_ok = _map_category(cat, title, cat_byname)
        to_create.append({
            "line": i, "sku": sku, "title": title, "cat_id": cat_id,
            "cat_matched": cat_ok, "qbo_cat": cat,
            "price": price, "cost": cost, "qty": counted,
            "pref_vendor": (r.get("Preferred Vendor") or "").strip(),
        })

    # ── Aggregate opening-stock plan (one entry PER PRODUCT) ─────────────────
    # Several QuickBooks rows can resolve to the same ERP product (legacy dupes).
    # Loading each row would stack conflicting deltas, so collapse per product:
    # take the MAX counted and flag any disagreement for the owner (never guess
    # silently). New products are unique per create, so they never collide here.
    stock_plan: dict = {}   # key -> {counted,cost,lines,is_new,pid,conflict}
    for c in (x for x in to_create if x["qty"] > 0):
        stock_plan[("new", c["line"])] = {
            "counted": c["qty"], "cost": c["cost"], "lines": [c["line"]],
            "is_new": True, "pid": None, "conflict": False,
        }
    for e in (x for x in existing_hits if x["qty"] > 0):
        key = ("pid", e["erp_pid"])
        pe = stock_plan.get(key)
        if pe is None:
            stock_plan[key] = {"counted": e["qty"], "cost": e["cost"],
                               "lines": [e["line"]], "is_new": False,
                               "pid": e["erp_pid"], "conflict": False}
        else:
            pe["lines"].append(e["line"])
            if e["qty"] != pe["counted"]:
                pe["conflict"] = True
                pe["counted"] = max(pe["counted"], e["qty"])
            if pe["cost"] is None:
                pe["cost"] = e["cost"]

    conflicts = [v for v in stock_plan.values() if v["conflict"]]
    uncategorized = [c for c in to_create if c["cat_id"] is None]
    stock_units = sum(v["counted"] for v in stock_plan.values())
    stock_val = sum((v["cost"] or 0.0) * v["counted"] for v in stock_plan.values())

    print(f"records parsed        : {len(rows)}")
    print(f"TO CREATE (new)       : {len(to_create)}   ({len(uncategorized)} uncategorized)")
    print(f"already in ERP (skip) : {len(existing_hits)}")
    print(f"flagged for review    : {len(flagged)}")
    print(f"bookkeeping skipped   : {len(bookkeeping)}")
    print(f"in-file duplicates    : {len(in_file_dup)}")
    print(f"price withheld (typo?): {len(price_review)}")
    print(f"opening stock loads   : {len(stock_plan)} products, {stock_units} units, "
          f"~${stock_val:,.2f} valuation"
          + (f"   ⚠ {len(conflicts)} qty CONFLICT(S)" if conflicts else "") + "\n")

    if conflicts:
        _c = "⚠ QUANTITY CONFLICTS (same product, disagreeing QBO rows — loaded the MAX; verify)"
        print("─" * 78 + f"\n{_c}\n" + "─" * 78)
        for v in conflicts:
            print(f"  ERP #{v['pid']}  lines {v['lines']}  -> loading {v['counted']} (max)")
        print()

    def _head(t):
        print("─" * 78 + f"\n{t}\n" + "─" * 78)

    _head("NEW PRODUCTS TO CREATE (keep QBO SKU)")
    for c in to_create:
        cflag = "" if c["cat_matched"] else "  [uncategorized]"
        p = f"${c['price']:,.2f}" if c["price"] is not None else "(no price)"
        q = f"  qty {c['qty']}@${c['cost']:,.2f}" if c["qty"] > 0 else ""
        print(f"  L{c['line']:<4} {c['sku']:<26} {p:>11}{q}{cflag}  {c['title'][:46]!r}")

    if price_review:
        _head("PRICE WITHHELD (created, but suspicious price NOT set — you set it)")
        for i, s, pr, co, note in price_review:
            print(f"  L{i:<4} {s:<26} {note}")

    _head("FLAGGED FOR REVIEW (SKU unusable — not imported, you decide)")
    for i, n, s, why in flagged:
        print(f"  L{i:<4} {s!r:<30} — {why}  :: {n[:40]!r}")

    _head("IN-FILE DUPLICATES (kept the first, skipped these)")
    for i, s, why in in_file_dup:
        print(f"  L{i:<4} {s!r:<26} — {why}")

    _head("BOOKKEEPING / SERVICE ROWS SKIPPED")
    for i, n, t in bookkeeping:
        print(f"  L{i:<4} [{t}] {n[:56]!r}")

    _head("ALREADY IN ERP — LEFT UNTOUCHED (stock rows will still get opening qty)")
    for e in existing_hits:
        tag = f"  << opening qty {e['qty']}" if e["qty"] > 0 else ""
        print(f"  L{e['line']:<4} {e['qbo_sku']:<26} -> ERP #{e['erp_pid']} {e['erp_sku']!r}{tag}")

    # Per-row decision CSV
    report_path = args.report or os.path.join(
        os.path.dirname(db_abs), f"qbo_import_decisions_{datetime.now().strftime('%Y%m%d')}.csv")
    with open(report_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["line", "decision", "qbo_sku", "title_or_reason", "qty", "cost", "price", "erp_match"])
        for c in to_create:
            w.writerow([c["line"], "CREATE", c["sku"], c["title"], c["qty"], c["cost"], c["price"], ""])
        for e in existing_hits:
            w.writerow([e["line"], "EXISTING_SKIP", e["qbo_sku"], "", e["qty"], e["cost"], "",
                        f"#{e['erp_pid']} {e['erp_sku']}"])
        for i, n, s, why in flagged:
            w.writerow([i, "FLAGGED", s, why, "", "", "", ""])
        for i, s, why in in_file_dup:
            w.writerow([i, "IN_FILE_DUP", s, why, "", "", "", ""])
        for i, s, pr, co, note in price_review:
            w.writerow([i, "PRICE_WITHHELD", s, note, "", co, pr, ""])
        for i, n, t in bookkeeping:
            w.writerow([i, "BOOKKEEPING_SKIP", "", n, "", "", "", ""])
    print(f"\nPer-row decision CSV written: {report_path}")

    if not args.apply:
        print("\nDRY-RUN complete — nothing written. Review the buckets above, then re-run with\n"
              '  --apply --confirm "IMPORT QBO PRODUCTS"')
        db.close()
        return 0

    # ── APPLY ────────────────────────────────────────────────────────────────
    psvc = ProductService(db, current_user_id=None)
    isvc = InventoryService(db, current_user_id=None)
    created_ids: dict[int, int] = {}  # csv line -> product id
    create_fail: list[tuple] = []
    for c in to_create:
        data = {
            "sku": c["sku"], "title": c["title"], "category_id": c["cat_id"],
            "price_override": c["price"], "cost": (c["cost"] or 0.0),
            "internal_notes": (f"Imported from QuickBooks 2026-07-07. "
                               f"QBO cost ${c['cost']}, preferred vendor "
                               f"{c['pref_vendor'] or '—'}."),
        }
        try:
            prod = psvc.create_product(data)
            created_ids[c["line"]] = prod.id
        except Exception as exc:  # noqa: BLE001
            create_fail.append((c["line"], c["sku"], str(exc)))
    print(f"created products      : {len(created_ids)}"
          + (f"   ({len(create_fail)} failed)" if create_fail else ""))
    for i, s, e in create_fail:
        print(f"  FAILED L{i} {s!r}: {e}")

    # Opening stock loads (ledger-backed): new products + existing matches w/ qty.
    def _load(pid, counted, qbo_cost, label):
        prod = db.query(Product).filter(Product.id == pid).first()
        if prod is None:
            return None
        before = prod.qty_on_hand or 0
        delta = counted - before
        if delta == 0:
            return "noop"
        # Never overwrite an existing real cost basis; seed one only when absent.
        unit_cost = qbo_cost if ((prod.cost or 0) == 0 and qbo_cost and qbo_cost > 0) else None
        isvc.adjust_inventory(
            product_id=pid, qty_delta=delta,
            reason=AdjustmentReason.INITIAL_INVENTORY_LOAD,
            note=f"QBO opening inventory {label} — counted {counted} (was {before})",
            unit_cost=(unit_cost if delta > 0 else None),
        )
        return "applied"

    loaded = 0
    for key, v in stock_plan.items():
        pid = created_ids.get(v["lines"][0]) if v["is_new"] else v["pid"]
        if not pid:
            continue
        label = "(new)" if v["is_new"] else "(existing)"
        if _load(pid, v["counted"], v["cost"], label) == "applied":
            loaded += 1
    db.commit()
    print(f"opening stock applied : {loaded} products")
    print("\nAPPLY complete (committed).")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
