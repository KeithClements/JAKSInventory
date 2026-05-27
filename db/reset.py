"""Reset (wipe) inventory tables for the configured database.

This is intended for development / re-running migrations.

Usage:
  # Uses DATABASE_URL from environment/.env
  python -m db.reset

Safety:
  You must type an exact confirmation phrase before any data is deleted.
"""

from __future__ import annotations

import argparse
import os
import secrets

from .database import get_db, get_database_url, parse_database_url


def reset_inventory_tables(*, force: bool = False) -> None:
    """Delete/truncate all inventory tables (products, part_numbers, xref_links, product_images).

    Safety model:
      - Does nothing unless force=True
      - Requires typing a random, one-time challenge code
    """
    if not force:
        print("Refusing to reset without --force.")
        return

    url = get_database_url()
    cfg = parse_database_url(url)

    if cfg["backend"] == "sqlite":
        target = cfg.get("path", "(unknown)")
        confirmation = f"RESET SQLITE {target}"
    else:
        target = f"{cfg.get('host')}:{cfg.get('port')}/{cfg.get('database')}"
        confirmation = f"RESET POSTGRES {target}"

    print("=" * 60)
    print("DANGEROUS: This will DELETE inventory tables")
    print("=" * 60)
    print(f"Target: {target}")
    print()
    challenge = secrets.token_hex(3).upper()  # 6 hex chars
    print("To continue:")
    print(f"  1) Type this EXACTLY: {confirmation}")
    print(f"  2) Then type this one-time code: {challenge}")
    typed_confirmation = input("> Confirmation: ").strip()
    if typed_confirmation != confirmation:
        print("Aborted (confirmation did not match).")
        return
    typed_challenge = input("> Code: ").strip().upper()
    if typed_challenge != challenge:
        print("Aborted (code did not match).")
        return

    with get_db(url) as db:
        if cfg["backend"] == "postgresql":
            # CASCADE handles foreign keys; RESTART IDENTITY resets serial sequences.
            db.execute(
                "TRUNCATE TABLE product_images, xref_links, part_numbers, products RESTART IDENTITY CASCADE"
            )
            db.commit()
        else:
            # SQLite: delete children first, then parents.
            db.execute("DELETE FROM product_images")
            db.execute("DELETE FROM xref_links")
            db.execute("DELETE FROM part_numbers")
            db.execute("DELETE FROM products")
            db.commit()

    print("Reset complete.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset (wipe) inventory tables for the configured database.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Actually perform the reset (required).",
    )
    args = parser.parse_args()

    # Help when someone runs without env configured.
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("Note: DATABASE_URL not set; using default (SQLite output/inventory.db).")

    reset_inventory_tables(force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
