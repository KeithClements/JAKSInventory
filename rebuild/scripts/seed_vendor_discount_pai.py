"""
scripts/seed_vendor_discount_pai.py
===================================
Seed (or update) PAI's volume-discount program on the LIVE data/jaks.db so the
"Apply 5% discount" button appears on PAI purchase orders over the threshold.

The rule reuses the existing vendor_programs table (no schema change). Idempotent:
re-running updates the same program row rather than creating duplicates — it is
matched by (vendor, program_type=tier_discount). Owner can also edit it in the UI
at /vendors/<id> → "Discount Programs".

Run (server STOPPED is fine; this only writes one row):
  .venv\\Scripts\\python.exe scripts\\seed_vendor_discount_pai.py            # PAI, 5%, $5,000
  .venv\\Scripts\\python.exe scripts\\seed_vendor_discount_pai.py PAI 5 15000  # override threshold
Args (all optional): VENDOR_CODE  PERCENT  THRESHOLD
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.database  # noqa: F401,E402 — binds the engine to the live data/jaks.db
from app.database import SessionLocal  # noqa: E402
from app.models.vendor import Vendor, VendorProgram  # noqa: E402
from app.constants import VendorProgramType  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    code = (args[0] if len(args) > 0 else "PAI").strip().upper()
    pct = float(args[1]) if len(args) > 1 else 5.0
    threshold = float(args[2]) if len(args) > 2 else 5000.0

    db = SessionLocal()
    try:
        vendor = (
            db.query(Vendor)
            .filter(Vendor.vendor_code.ilike(code))
            .first()
        )
        if vendor is None:
            vendor = db.query(Vendor).filter(Vendor.name.ilike(f"%{code}%")).first()
        if vendor is None:
            raise SystemExit(
                f"No vendor found with code/name matching '{code}'. "
                f"Check /vendors and pass the right code."
            )

        program = (
            db.query(VendorProgram)
            .filter(
                VendorProgram.vendor_id == vendor.id,
                VendorProgram.program_type == VendorProgramType.TIER_DISCOUNT,
            )
            .first()
        )
        name = f"Volume discount — {pct:g}% over ${threshold:,.0f}"
        if program is None:
            program = VendorProgram(
                vendor_id=vendor.id,
                program_name=name,
                program_type=VendorProgramType.TIER_DISCOUNT,
                threshold_amount=threshold,
                discount_percent=pct,
                is_active=True,
            )
            db.add(program)
            action = "Created"
        else:
            program.program_name = name
            program.threshold_amount = threshold
            program.discount_percent = pct
            program.is_active = True
            action = "Updated"
        db.commit()

        print(f"{action} discount program for {vendor.name} (id={vendor.id}):")
        print(f"  {pct:g}% off all parts when a PO exceeds ${threshold:,.0f}")
        print(f"\nEdit it anytime at: /vendors/{vendor.id}  ->  Discount Programs")
    finally:
        db.close()


if __name__ == "__main__":
    main()
