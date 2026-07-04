"""
scripts/audit_locked_margins.py
===============================
Locked-price margin alert (see app/services/pricing_audit.py). A hand-edited price
is locked against the scraper — but vendor cost keeps moving underneath it, so a
locked price can silently sink below your margin floor (or below cost). This finds
them.

Default is a READ-ONLY report. Pass --apply to flag each as needs_review and record
the count for the Settings badge.

Run (report):  .venv\\Scripts\\python.exe -m scripts.audit_locked_margins
Run (apply):   .venv\\Scripts\\python.exe -m scripts.audit_locked_margins --apply
Options:
  --apply         flag flagged products needs_review + record the alert count
  --floor N       margin floor % (default: shopify_margin_floor_pct setting, else 15)
"""
from __future__ import annotations

import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    floor = None
    if "--floor" in argv:
        i = argv.index("--floor")
        if i + 1 < len(argv):
            try:
                floor = float(argv[i + 1])
            except ValueError:
                pass

    from app.database import SessionLocal
    from app.services.pricing_audit import audit_locked_margins

    db = SessionLocal()
    try:
        res = audit_locked_margins(db, floor_pct=floor, apply=apply)
        print(f"\n== Locked-price margin audit (floor {res['floor_pct']:.1f}%) ==")
        print(f"  flagged below floor : {res['count']}")
        print(f"  of which below cost : {res['below_cost']}")
        for f in res["flagged"][:40]:
            tag = " BELOW COST" if f["below_cost"] else ""
            print(f"    - {f['sku']:<22} price ${f['price']:.2f} cost ${f['cost']:.2f} "
                  f"margin {f['margin_pct']:.1f}%{tag}")
        if len(res["flagged"]) > 40:
            print(f"    ...and {len(res['flagged']) - 40} more")
        if apply:
            print(f"\nFlagged {res['count']} product(s) needs_review.")
        else:
            print("\nRead-only. Re-run with --apply to flag these needs_review.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
