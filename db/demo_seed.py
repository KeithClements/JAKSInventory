"""Seed the inventory database with demo data.

This is used to quickly validate the webapp and DB connectivity.
It inserts demo products/part numbers/xref links if they don't already exist.

Usage:
  python -m db.demo_seed --count 25
"""

from __future__ import annotations

import argparse
from datetime import datetime

from db import get_db, init_db
from db.database import is_postgresql


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def seed_demo(*, count: int) -> dict:
    init_db()
    created_products = 0
    created_parts = 0
    created_xrefs = 0
    skipped_existing = 0

    categories = [
        "Engine Rebuild Kits",
        "Turbochargers",
        "Fuel Injectors",
        "Gaskets",
        "Cooling System",
    ]
    manufacturers = ["Cummins", "Detroit", "Caterpillar", "International", "Volvo"]

    with get_db() as db:
        # Detect optional columns so the seed works on databases that haven't
        # run later migrations (e.g. lightweight unit tests).
        try:
            existing_cols = {
                row[1] if not hasattr(row, "keys") else row["name"]
                for row in db.execute("PRAGMA table_info(products)").fetchall()
            }
        except Exception:
            existing_cols = set()
        if not existing_cols:
            try:
                rows = db.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'products'"
                ).fetchall()
                existing_cols = {
                    r[0] if not hasattr(r, "keys") else r["column_name"]
                    for r in rows
                }
            except Exception:
                existing_cols = set()
        has_core_sell_price = (not existing_cols) or ("core_sell_price" in existing_cols)

        for i in range(1, max(1, count) + 1):
            part = f"DEMO{i:04d}"
            sku = f"JAKS-{part}"

            existing = db.execute("SELECT id FROM products WHERE sku = %s", (sku,)).fetchone()
            if existing:
                skipped_existing += 1
                continue

            cat = categories[(i - 1) % len(categories)]
            mfr = manufacturers[(i - 1) % len(manufacturers)]
            title = f"Demo Part {part} ({mfr})"
            url = f"https://example.invalid/product/{part.lower()}"
            handle = part.lower()

            # Every 3rd product is a "core-bearing" item so demo invoices
            # actually exercise the core-return paperwork flow.
            if i % 3 == 0:
                core_charge = round(75 + (i * 7.5) % 200, 2)   # vendor deposit
                core_sell_price = round(core_charge * 1.15, 2)  # customer charge
            else:
                core_charge = 0
                core_sell_price = 0

            product_row = {
                "sku": sku,
                "handle": handle,
                "url": url,
                "title": title,
                "category": cat,
                "manufacturer": mfr,
                "supplier": "DEMO",
                "engine": "",
                "condition": "New",
                "cost": round(50 + i * 1.1, 2),
                "pai_cost": round(45 + i * 1.0, 2),
                "selling_price": round(99 + i * 2.0, 2),
                "compare_at_price": 0,
                "core_charge": core_charge,
                "core_sell_price": core_sell_price,
                "pai_sku": "",
                "pai_status": "",
                "pai_stock": "",
                "pai_link": "",
                "description_html": f"<p>Demo description for {sku}</p>",
                "kit_contents": "[]",
                "image_count": 0,
                "stage": 1,
                "shopify_id": None,
                "updated_at": _now_iso(),
                "scraped_at": None,
                "qty_on_hand": 10,
                "reorder_point": 2,
            }

            if not has_core_sell_price:
                product_row.pop("core_sell_price", None)

            cols = ", ".join(product_row.keys())
            placeholders = ", ".join(["%s"] * len(product_row))

            if is_postgresql():
                cursor = db.execute(
                    f"INSERT INTO products ({cols}) VALUES ({placeholders}) RETURNING id",
                    tuple(product_row.values()),
                )
                row = cursor.fetchone()
                product_id = row[0] if row else 0
            else:
                cursor = db.execute(
                    f"INSERT INTO products ({cols}) VALUES ({placeholders})",
                    tuple(product_row.values()),
                )
                product_id = cursor.lastrowid

            created_products += 1

            # Part numbers: primary + OEM.
            primary = {
                "product_id": product_id,
                "number": part,
                "number_normalized": part,
                "type": "PRIMARY",
                "manufacturer": mfr,
                "is_primary": True,
                "source": "DEMO",
            }
            oem_num = f"OEM-{part}"
            oem = {
                "product_id": product_id,
                "number": oem_num,
                "number_normalized": oem_num.replace("-", ""),
                "type": "OEM",
                "manufacturer": mfr,
                "is_primary": False,
                "source": "DEMO",
            }

            def insert_part(row: dict) -> int:
                cols2 = ", ".join(row.keys())
                placeholders2 = ", ".join(["%s"] * len(row))
                if is_postgresql():
                    cur2 = db.execute(
                        f"INSERT INTO part_numbers ({cols2}) VALUES ({placeholders2}) RETURNING id",
                        tuple(row.values()),
                    )
                    r2 = cur2.fetchone()
                    return int(r2[0]) if r2 else 0
                cur2 = db.execute(
                    f"INSERT INTO part_numbers ({cols2}) VALUES ({placeholders2})",
                    tuple(row.values()),
                )
                return int(cur2.lastrowid or 0)

            primary_id = insert_part(primary)
            oem_id = insert_part(oem)
            created_parts += 2

            # Cross-link primary <-> OEM for testing xref search.
            if primary_id and oem_id:
                db.execute(
                    """INSERT INTO xref_links (part_a_id, part_b_id, source, confidence)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT DO NOTHING""",
                    (primary_id, oem_id, "DEMO", "high"),
                )
                created_xrefs += 1

        db.commit()

    return {
        "created_products": created_products,
        "created_part_numbers": created_parts,
        "created_xref_links": created_xrefs,
        "skipped_existing": skipped_existing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the configured DB with demo inventory data.")
    parser.add_argument("--count", type=int, default=25, help="How many demo products to insert")
    args = parser.parse_args()

    stats = seed_demo(count=max(1, args.count))
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
