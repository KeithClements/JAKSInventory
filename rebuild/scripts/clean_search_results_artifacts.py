"""
scripts/clean_search_results_artifacts.py
=========================================
One-off catalog hygiene: purge the "N Search Results For X" scraper artifact that
leaked into ~97 PAI product titles / SEO descriptions and out onto the live
Shopify storefront (surfaced in the 2026-07-06 storefront review).

Two distinct problems, handled separately:

  KEEPERS  — a real PAI part number (e.g. 311133, 594-A, FUL75120) whose title
             never got a real name scraped, so it reads
             "311133 Caterpillar 2 Search Results For 311133 | PAI | JAK's Diesel".
             FIX: strip the "N Search Results For X" fragment and the leaked
             "| PAI |" from the LIVE Shopify title (this preserves the engine
             make that lives only on Shopify), rewrite the SEO description to a
             clean generic, and mirror a cleaned title into the ERP DB so a
             future push stays clean.

  GARBAGE  — the "part number" is itself a scrape artifact: a bare 1-2 digit
             browse token ("1", "22"), an "N new" fragment, or a dictionary word
             ("JOINT"), evidenced by a huge result count. These are not real,
             orderable parts.
             FIX: unpublish on Shopify (status DRAFT) and deactivate in the ERP
             (status=inactive, is_active=0). Fully reversible.

Safety:
  * Dry-run by default — prints the full classification + before/after titles and
    changes NOTHING. Pass --apply to write.
  * DB write is short-transaction + WAL busy_timeout (safe while the app runs).
  * Idempotent: a second run finds nothing (no title/seo contains "Search
    Results" after apply).
  * Fail-soft per product — one Shopify error is logged and skipped, never aborts
    the batch (mirrors the per-row HOLD discipline in the importer).

Run (preview):  .venv\\Scripts\\python.exe scripts\\clean_search_results_artifacts.py
Run (apply):    .venv\\Scripts\\python.exe scripts\\clean_search_results_artifacts.py --apply
"""
from __future__ import annotations

import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_LOG = pathlib.Path(__file__).resolve().parent / "clean_search_results_artifacts.log"

# "N Search Results For <token>" — the token runs to the next pipe or end.
_FRAG_RE = re.compile(r"\s*[\d,]+\s+Search\s+Results\s+For\s+[^|]*", re.I)
# Leaked supplier segment "| PAI |" (or a trailing "| PAI")
_PAI_SEG_RE = re.compile(r"\s*\|\s*PAI\s*(?=\||$)", re.I)
# Leading result count in the raw DB title ("3,074 Search Results for 1")
_COUNT_RE = re.compile(r"^\s*([\d,]+)\s+Search\s+Results\s+for", re.I)

_STORE_SUFFIX_RE = re.compile(r"\s*\|\s*JAK'?S?\s+Diesel\s*$", re.I)

_PHONE = "(720) 445-6249"


def _log(msg: str) -> None:
    print(msg, flush=True)
    with _LOG.open("a", encoding="utf-8") as fh:
        fh.write(msg.rstrip() + "\n")


def _parse_count(db_title: str) -> int:
    m = _COUNT_RE.search(db_title or "")
    if not m:
        return 0
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return 0


def _is_garbage(vpn: str, count: int) -> bool:
    """A part number that is really a scrape/browse token, not a real part."""
    v = (vpn or "").strip().lower()
    if not v:
        return True
    if re.fullmatch(r"\d{1,2}", v):          # "1", "22"
        return True
    if re.search(r"\bnew\b", v):             # "1 new", "6 new"
        return True
    if v.isalpha() and len(v) >= 3:          # "joint", generic word
        return True
    if count >= 25:                          # matched dozens/hundreds → browse token
        return True
    return False


def _clean_live_title(live_title: str, vpn: str) -> str:
    """Strip the junk fragment + PAI leak from a LIVE Shopify title, keeping the
    engine make that only exists there. Falls back to a readable stem."""
    t = _FRAG_RE.sub(" ", live_title or "")
    t = _PAI_SEG_RE.sub("", t)
    # normalize pipe spacing and collapse whitespace
    t = re.sub(r"\s*\|\s*", " | ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" |").strip()
    # if nothing meaningful survived (title was only the junk), build a stem
    stem = _STORE_SUFFIX_RE.sub("", t).strip(" |").strip()
    if not stem or re.fullmatch(r"[\d,]*", stem):
        base = f"{vpn}".strip()
        t = f"{base} | JAK's Diesel" if base else "JAK's Diesel Part"
    return t


def _db_title_from(clean_live_title: str, vpn: str) -> str:
    """Bare product name for the ERP DB (no store suffix)."""
    stem = _STORE_SUFFIX_RE.sub("", clean_live_title or "").strip(" |").strip()
    stem = _PAI_SEG_RE.sub("", stem).strip(" |").strip()
    if not stem or re.fullmatch(r"[\d,]*", stem):
        return (vpn or "").strip() or "Diesel Part"
    return stem


def _clean_seo_desc(vpn: str) -> str:
    return (f"Part #{vpn} — 2-year warranty, same-day shipping from JAK's Diesel. "
            f"Call {_PHONE} to confirm fitment for your engine.")


_NODES_QUERY = (
    "query($ids:[ID!]!){ nodes(ids:$ids){ ... on Product { id title "
    "seo { title description } } } }"
)
_PRODUCT_UPDATE = (
    "mutation($input: ProductInput!){ productUpdate(input:$input){ "
    "product{ id } userErrors{ field message } } }"
)


def main() -> int:
    apply = "--apply" in sys.argv
    from sqlalchemy import text
    from app.database import SessionLocal
    from app.models.product import Product
    from app.constants import ProductStatus
    from app.services.shopify_service import ShopifyService

    db = SessionLocal()
    try:
        db.execute(text("PRAGMA journal_mode=WAL"))
        db.execute(text("PRAGMA busy_timeout=30000"))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        _log(f"WARN wal: {exc}")

    svc = ShopifyService(db, None)
    configured = svc.is_configured()
    _log(f"=== clean_search_results_artifacts  apply={apply}  shopify_configured={configured} ===")

    rows = (db.query(Product)
            .filter(Product.title.like("%Search Results%")
                    | Product.seo_description.like("%Search Results%"))
            .order_by(Product.id).all())
    _log(f"contaminated products: {len(rows)}")

    # resolve vendor part number per product (preferred active source, else any)
    def vpn_of(p: Product) -> str:
        src = p.preferred_vendor_source
        if not src:
            src = next((s for s in p.vendor_sources if s.is_active), None)
        if src and (src.vendor_part_number or "").strip():
            return src.vendor_part_number.strip()
        # fall back to the trailing SKU segment (JAKS-PAI-311133 -> 311133)
        return (p.sku or "").split("-")[-1].strip()

    # fetch live Shopify titles in bulk (chunks of 50)
    live: dict[str, dict] = {}
    if configured:
        gids = [p.shopify_product_id for p in rows if (p.shopify_product_id or "").startswith("gid://")]
        for i in range(0, len(gids), 50):
            chunk = gids[i:i + 50]
            data = svc._graphql(_NODES_QUERY, {"ids": chunk})
            for n in (data.get("data") or {}).get("nodes") or []:
                if n and n.get("id"):
                    live[n["id"]] = n
        _log(f"fetched {len(live)} live listings")

    keepers, garbage = [], []
    for p in rows:
        vpn = vpn_of(p)
        count = _parse_count(p.title)
        gid = p.shopify_product_id or ""
        lt = (live.get(gid) or {}).get("title") or ""
        plan = {"p": p, "vpn": vpn, "count": count, "gid": gid, "live_title": lt}
        if _is_garbage(vpn, count):
            garbage.append(plan)
        else:
            new_live = _clean_live_title(lt or p.title, vpn)
            plan["new_live_title"] = new_live
            plan["new_db_title"] = _db_title_from(new_live, vpn)
            plan["new_seo_desc"] = _clean_seo_desc(vpn)
            keepers.append(plan)

    _log(f"\n--- KEEPERS ({len(keepers)}): clean title, keep live ---")
    for pl in keepers:
        _log(f"  [{pl['p'].id}] {pl['vpn']:<16} live: {pl['live_title'][:70]!r}")
        _log(f"        -> live_title: {pl['new_live_title'][:70]!r}   db_title: {pl['new_db_title'][:50]!r}")

    _log(f"\n--- GARBAGE ({len(garbage)}): unpublish + deactivate ---")
    for pl in garbage:
        _log(f"  [{pl['p'].id}] vpn={pl['vpn']!r} count={pl['count']} title={pl['p'].title[:50]!r}")

    if not apply:
        _log("\nDRY RUN — no changes written. Re-run with --apply to execute.")
        return 0

    # ── APPLY ────────────────────────────────────────────────────────────────
    ok_live = ok_db = fail = 0
    # keepers: update live title + seo, then DB
    for pl in keepers:
        p = pl["p"]
        if configured and pl["gid"].startswith("gid://"):
            inp = {"id": pl["gid"], "seo": {"description": pl["new_seo_desc"]}}
            # Only overwrite the live title when we actually improved it — never
            # touch a title Shopify already shows clean (e.g. "WATER PUMP").
            if pl["new_live_title"].strip() and pl["new_live_title"].strip() != (pl["live_title"] or "").strip():
                inp["title"] = pl["new_live_title"]
            res = svc._graphql(_PRODUCT_UPDATE, {"input": inp})
            ue = (((res.get("data") or {}).get("productUpdate") or {}).get("userErrors")
                  or res.get("errors"))
            if ue:
                fail += 1
                _log(f"  LIVE-FAIL [{p.id}]: {ue}")
            else:
                ok_live += 1
            time.sleep(0.1)
        # Only rewrite the DB title when it is itself contaminated or empty —
        # a keeper can be in the list solely for a dirty seo_description.
        if "search results" in (p.title or "").lower() or not (p.title or "").strip():
            p.title = pl["new_db_title"]
        if "search results" in (p.seo_description or "").lower():
            p.seo_description = pl["new_seo_desc"]
        ok_db += 1
    db.commit()

    # garbage: unpublish live + deactivate DB
    for pl in garbage:
        p = pl["p"]
        if configured and pl["gid"].startswith("gid://"):
            inp = {"id": pl["gid"], "status": "DRAFT"}
            res = svc._graphql(_PRODUCT_UPDATE, {"input": inp})
            ue = (((res.get("data") or {}).get("productUpdate") or {}).get("userErrors")
                  or res.get("errors"))
            if ue:
                fail += 1
                _log(f"  LIVE-FAIL [{p.id}]: {ue}")
            else:
                ok_live += 1
            time.sleep(0.1)
        # strip junk from the DB title even while inactive, for hygiene
        p.title = _FRAG_RE.sub(" ", p.title or "").strip() or (pl["vpn"] or "Inactive Part")
        p.status = ProductStatus.INACTIVE
        p.is_active = False
        p.shopify_status = "DRAFT"
        ok_db += 1
    db.commit()

    _log(f"\n=== APPLIED: live_updated={ok_live}  db_updated={ok_db}  failed={fail} ===")
    _log(f"    keepers cleaned={len(keepers)}  garbage unpublished={len(garbage)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
