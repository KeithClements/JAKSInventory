"""
scripts/set_vendor_private_label.py
===================================
Flag one or more vendors as PRIVATE LABEL — their products sell as our house brand
and the customer-facing SKU hides the vendor code (uses the house prefix instead,
e.g. JAKS-JAKS-10R1273). Also applies the Alembic column migration if the live DB
predates it.

After running this, run scripts/regenerate_skus_vendor.py to rewrite the affected
vendors' existing SKUs to the masked {prefix}-{prefix}-{part#} form (it backs up
first and flags those products is_house_brand).

Run (server STOPPED):
  .venv\\Scripts\\python.exe scripts\\set_vendor_private_label.py DFT MIG
Default codes (no args): DFT MIG.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "jaks.db"

import app.database  # noqa: F401,E402 — binds the engine to the live data/jaks.db


def main() -> None:
    codes = [c.strip().upper() for c in (sys.argv[1:] or ["DFT", "MIG"])]
    if not DB.exists():
        raise SystemExit(f"DB not found: {DB}")

    # 1. Ensure the schema has vendors.private_label (idempotent; no-op if present).
    from alembic import command
    from app.db_migrate import _config
    command.upgrade(_config(), "head")
    print("Alembic upgraded to head (vendors.private_label present).")

    # 2. Flag the vendors.
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    qmarks = ",".join("?" * len(codes))
    n = cur.execute(
        f"UPDATE vendors SET private_label = 1 "
        f"WHERE UPPER(vendor_code) IN ({qmarks})", codes,
    ).rowcount
    conn.commit()
    print(f"Flagged {n} vendor(s) private_label:")
    for code, name, pl in cur.execute(
        f"SELECT vendor_code, name, private_label FROM vendors "
        f"WHERE UPPER(vendor_code) IN ({qmarks})", codes,
    ):
        print(f"  {code} | {name} | private_label={pl}")
    conn.close()
    print("\nNext: .venv\\Scripts\\python.exe scripts\\regenerate_skus_vendor.py")


if __name__ == "__main__":
    main()
