"""
scripts/backfill_seo.py
=======================
Generate + persist an AI SEO meta-description (and SEO title when blank) for every
active product that lacks one, so the whole catalog is search-ready. Writes to
Product.seo_description; the next Shopify sync pushes it to the storefront's native
SEO field automatically (ShopifyService._build_listing).

Uses Claude via AIDescriptionService — so it COSTS Anthropic tokens (the summary
reports the total). Default is a READ-ONLY count; pass --apply to generate. The run
commits per product, so it is resumable — kill and re-run, already-filled products
are skipped.

NOTE: the Anthropic key is Fernet-encrypted, so JAKS_FERNET_KEY must be set (load it
the same way the ERP launcher does, from %USERPROFILE%\\.jaks_fernet.key).

Run (count):     .venv\\Scripts\\python.exe -m scripts.backfill_seo
Run (apply):     .venv\\Scripts\\python.exe -m scripts.backfill_seo --apply
Options:
  --apply        actually generate + write (default: dry-run count)
  --limit N      only process the first N products needing SEO (good for a trial)
  --overwrite    regenerate even products that already have a seo_description
  --ids 1,2,3    restrict to specific product ids
"""
from __future__ import annotations

import pathlib
import sys

# Force UTF-8 stdout so a legacy .bat console (cp1252) never crashes on output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def _arg(argv: list[str], name: str) -> str | None:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    overwrite = "--overwrite" in argv
    limit = _arg(argv, "--limit")
    ids_raw = _arg(argv, "--ids")
    limit_n = int(limit) if (limit and limit.isdigit()) else None
    ids = [int(x) for x in ids_raw.split(",") if x.strip().isdigit()] if ids_raw else None

    from app.database import SessionLocal
    from app.services.ai_description_service import AIDescriptionService

    db = SessionLocal()
    try:
        svc = AIDescriptionService(db, None)
        if not svc.is_configured():
            print("Anthropic API key not set (or JAKS_FERNET_KEY missing so it can't "
                  "decrypt). Set it in Settings -> AI, and export JAKS_FERNET_KEY.")
            return 2
        print(f"Model: {svc.model()}")

        if not apply:
            res = svc.backfill_seo(ids, limit=limit_n, overwrite=overwrite, dry_run=True)
            print(f"\n{res['considered']} product(s) need an SEO description"
                  + (" (overwrite: ALL active)" if overwrite else "")
                  + ". Re-run with --apply to generate. This was a dry run.")
            return 0

        seen = {"n": 0}

        def _progress(msg: str) -> None:
            seen["n"] += 1
            if seen["n"] % 25 == 0:
                print(f"  ...{msg}")

        res = svc.backfill_seo(ids, limit=limit_n, overwrite=overwrite,
                               dry_run=False, progress=_progress)
        print("\n== SEO backfill ==")
        for k in ("ok", "considered", "generated", "skipped_have_seo", "failed",
                  "tokens_in", "tokens_out"):
            print(f"  {k}: {res.get(k)}")
        if res.get("errors"):
            print("  errors (first few):")
            for e in res["errors"]:
                print(f"    - {e}")
        if not res.get("ok"):
            print(f"\nStopped: {res.get('message', res.get('error'))}")
            return 1
        print(f"\nDone. Generated SEO for {res.get('generated', 0)} product(s).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
