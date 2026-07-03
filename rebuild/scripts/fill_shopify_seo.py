"""
scripts/fill_shopify_seo.py
===========================
Fill the native SEO meta-description on LIVE Shopify products that lack one, using
AI copy (AIDescriptionService / Claude) generated from each product's own
title + body + vendor + type. Writes ONLY seo.description via productUpdate — the
customer-facing title, body, images, price, tags and publish status are never
touched. Reads the gap list produced by scripts.scan_shopify_seo.

Needs JAKS_FERNET_KEY in the env (decrypts both the Shopify token and the
Anthropic key). Resumable: results are logged and already-filled ids are skipped.

Run (preview 8, no writes):  .venv\\Scripts\\python.exe -m scripts.fill_shopify_seo
Run (apply all):             .venv\\Scripts\\python.exe -m scripts.fill_shopify_seo --apply
Options: --apply  --limit N  --preview N
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

HERE = pathlib.Path(__file__).resolve().parent
GAP = HERE / "shopify_seo_gap.json"
RESULTS = HERE / "shopify_seo_fill_results.json"
SEO_MAX = 320  # Shopify seo.description ceiling

_PRODUCT_UPDATE = """
mutation($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id seo { description } }
    userErrors { field message }
  }
}
"""


_PHONE = "(720) 445-6249"


def _has_tag(tags, *kw) -> bool:
    low = [str(t).lower() for t in (tags or [])]
    return any(k.lower() in low for k in kw)


def _clean_title(title: str) -> str:
    t = (title or "").split(" | PAI")[0].strip()
    t = re.sub(r"\s*\|\s*JAK'?s Diesel\s*$", "", t, flags=re.IGNORECASE).strip()
    return t


def _template_meta(g: dict) -> str:
    """Free, no-AI SEO meta: keyword-rich part name up front + JAK's Diesel
    availability/shipping/phone suffix. Warranty claimed ONLY when the product is
    tagged for one (or is a JAK's/PAI house part) so third-party brands aren't
    over-promised. Trimmed so the brand+phone suffix always survives the snippet."""
    title = _clean_title(g.get("title") or "")
    vendor = (g.get("vendor") or "").strip()
    tags = g.get("tags") or []
    if _has_tag(tags, "3-Year Warranty", "3 Year Warranty"):
        suffix = f"3-yr warranty. Same-day shipping. JAK's Diesel — {_PHONE}."
    elif vendor == "PAI Industries" or _has_tag(tags, "2-Year Warranty", "2 Year Warranty"):
        suffix = f"2-yr warranty. Same-day shipping. JAK's Diesel — {_PHONE}."
    else:
        suffix = f"In stock, same-day shipping. JAK's Diesel — {_PHONE}."
    budget = 165 - len(suffix) - 2
    core = title
    if len(core) > budget:
        core = core[:budget].rsplit(" ", 1)[0].rstrip(".,;-— ") + "…"
    return f"{core}. {suffix}"[:SEO_MAX]


def _arg(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def main(argv) -> int:
    apply = "--apply" in argv
    template = "--template" in argv
    from_file = _arg(argv, "--from-file")   # JSON {gid: meta} authored in-session
    limit = _arg(argv, "--limit")
    limit_n = int(limit) if (limit and limit.isdigit()) else None
    preview_n = int(_arg(argv, "--preview", "8"))

    authored: dict[str, str] = {}
    if from_file:
        authored = json.loads(pathlib.Path(from_file).read_text(encoding="utf-8"))
        print(f"loaded {len(authored)} authored descriptions from {from_file}")

    if not GAP.exists():
        print(f"Gap file missing: {GAP}. Run scripts.scan_shopify_seo first.")
        return 1
    gap = json.loads(GAP.read_text(encoding="utf-8"))

    # Resume: skip ids already written successfully in a prior run.
    done_ids = set()
    if RESULTS.exists():
        try:
            for r in json.loads(RESULTS.read_text(encoding="utf-8")):
                if r.get("ok"):
                    done_ids.add(r["id"])
        except Exception:
            pass
    todo = [g for g in gap if g["id"] not in done_ids]
    if limit_n:
        todo = todo[:limit_n]

    from app.database import SessionLocal
    from app.services.ai_description_service import AIDescriptionService
    from app.services.shopify_service import ShopifyService

    db = SessionLocal()
    try:
        ai = AIDescriptionService(db, None)
        shop = ShopifyService(db, None)
        if not template and not from_file and not ai.is_configured():
            print("ABORT: Anthropic key not set/undecryptable (need JAKS_FERNET_KEY).")
            return 2
        if not shop.is_configured():
            print("ABORT: Shopify not configured.")
            return 2
        gen = ("AUTHORED in-session" if from_file
               else "TEMPLATE (free)" if template else f"AI {ai.model()}")
        print(f"generator: {gen}   |   store: {shop._store_domain()}")
        print(f"gap={len(gap)}  already_done={len(done_ids)}  to_process={len(todo)}"
              f"  mode={'APPLY' if apply else f'PREVIEW(first {preview_n})'}\n")

        results = []
        n_ok = n_fail = 0
        work = todo if apply else todo[:preview_n]
        for i, g in enumerate(work, 1):
            if from_file:
                meta = (authored.get(g["id"])
                        or authored.get(g["id"].split("/")[-1])
                        or "").strip()[:SEO_MAX]
                tok_note = "authored"
                if not meta:
                    # No authored copy for this id — skip (don't fall back silently).
                    results.append({"id": g["id"], "ok": False, "stage": "gen",
                                    "error": "no authored copy"})
                    continue
            elif template:
                meta = _template_meta(g)[:SEO_MAX]
                tok_note = "template"
            else:
                res = ai.suggest_for_product(
                    manufacturer=g.get("vendor") or None,
                    brand=g.get("vendor") or None,
                    category_path=g.get("product_type") or None,
                    current_title=g.get("title") or None,
                    current_description=g.get("description") or None,
                )
                if res.get("error"):
                    if res["error"] == "no_api_key":
                        print("FATAL: API key lost mid-run; stopping.")
                        break
                    n_fail += 1
                    print(f"  [{i}/{len(work)}] AI FAIL {g['title'][:50]}: {res.get('message')}")
                    results.append({"id": g["id"], "ok": False, "stage": "ai",
                                    "error": res.get("message")})
                    continue
                meta = (res.get("meta_description") or "").strip()[:SEO_MAX]
                tok_note = f"{res.get('tokens_in',0)}+{res.get('tokens_out',0)} tok"
            if not meta:
                n_fail += 1
                results.append({"id": g["id"], "ok": False, "stage": "gen",
                                "error": "empty meta"})
                continue

            if not apply:
                print(f"[{i}] {g['status']:5} | {g['title'][:60]}")
                print(f"     -> {meta}")
                print(f"        ({len(meta)} chars, {tok_note})\n")
                results.append({"id": g["id"], "ok": None, "preview": meta})
                continue

            # APPLY — write seo.description only.
            d = shop._graphql(_PRODUCT_UPDATE,
                              {"input": {"id": g["id"], "seo": {"description": meta}}})
            ue = (((d.get("data") or {}).get("productUpdate") or {}).get("userErrors")
                  or d.get("errors"))
            if ue:
                n_fail += 1
                print(f"  [{i}/{len(work)}] WRITE FAIL {g['title'][:45]}: {str(ue)[:120]}")
                results.append({"id": g["id"], "ok": False, "stage": "write",
                                "error": str(ue)[:200], "meta": meta})
            else:
                n_ok += 1
                results.append({"id": g["id"], "ok": True, "meta": meta})
                if n_ok % 25 == 0:
                    print(f"  ...{n_ok} written (fail {n_fail})", flush=True)
                    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
            time.sleep(0.2)  # gentle on the Shopify cost bucket

        if apply:
            # Merge with any prior results for an accurate resumable log.
            prior = []
            if RESULTS.exists():
                try:
                    prior = [r for r in json.loads(RESULTS.read_text(encoding="utf-8"))
                             if r.get("ok") and r["id"] not in {x["id"] for x in results}]
                except Exception:
                    pass
            RESULTS.write_text(json.dumps(prior + results, indent=2), encoding="utf-8")
            print(f"\n=== DONE ===  written={n_ok}  failed={n_fail}  "
                  f"(log: {RESULTS.name})")
        else:
            print(f"=== PREVIEW complete: {len(work)} generated, no writes. "
                  f"Re-run with --apply to write all {len(todo)}. ===")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
