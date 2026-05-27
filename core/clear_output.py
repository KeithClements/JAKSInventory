"""Clear scraper-generated files under output/.

This wipes the file-based artifacts the scraper writes (inventories, per-SKU
folders, images, caches, temp downloads). It does NOT delete the output/
directory itself.

Usage:
  python -m core.clear_output          # shows what would be deleted (no-op)
  python -m core.clear_output --force  # prompts for confirmation + one-time code
"""

from __future__ import annotations

import argparse
import secrets
import shutil
from pathlib import Path

from .paths import OUTPUT_ROOT


def _safe_delete_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def clear_output(*, force: bool = False) -> None:
    output_root = OUTPUT_ROOT
    if not output_root.exists():
        print(f"Nothing to clear: {output_root} does not exist")
        return

    # Build deletion list (everything under output/ except hidden files).
    targets: list[Path] = []
    for child in output_root.iterdir():
        # Keep dotfiles (e.g., .gitignore) if present.
        if child.name.startswith("."):
            continue
        targets.append(child)

    if not targets:
        print("Nothing to clear: output/ is already empty")
        return

    print("Will delete the following from output/:")
    for t in sorted(targets, key=lambda p: p.name.lower()):
        suffix = "/" if t.is_dir() else ""
        print(f"  - {t.as_posix()}{suffix}")

    if not force:
        print("\nRefusing to delete without --force.")
        return

    confirmation = f"CLEAR OUTPUT {output_root.resolve()}"
    code = secrets.token_hex(3).upper()

    print("\nDANGEROUS: This will permanently delete the listed files/folders.")
    print("To continue:")
    print(f"  1) Type this EXACTLY: {confirmation}")
    print(f"  2) Then type this one-time code: {code}")
    typed_confirmation = input("> Confirmation: ").strip()
    if typed_confirmation != confirmation:
        print("Aborted (confirmation did not match).")
        return
    typed_code = input("> Code: ").strip().upper()
    if typed_code != code:
        print("Aborted (code did not match).")
        return

    deleted = 0
    for t in targets:
        _safe_delete_path(t)
        deleted += 1

    output_root.mkdir(parents=True, exist_ok=True)
    print(f"Cleared output/: removed {deleted} item(s).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear scraper-generated artifacts under output/.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Actually delete files/folders (required for destructive action).",
    )
    args = parser.parse_args()

    clear_output(force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
