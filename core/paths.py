"""Path utilities for organized output structure.

Output folder structure:
    output/
    ├── <category>/
    │   └── <manufacturer>/
    │       ├── inventory.json
    │       └── <sku>/
    │           ├── product.json
    │           ├── description.html
    │           └── images/
    ├── scrapes/          # Legacy scoped scrapes
    ├── diffs/            # Export diffs
    ├── persistence/      # Legacy persistence
    └── master_inventory.json
"""

from __future__ import annotations
import re
from pathlib import Path

__all__ = [
    "OUTPUT_ROOT",
    "SCOPED_SCRAPES_DIR",
    "DIFF_EXPORTS_DIR",
    "PERSISTENCE_DIR",
    "slug_for_path",
    "get_sku_folder",
    "get_sku_images_folder",
]

# Root output directory
OUTPUT_ROOT = Path("output")

# Legacy directories (kept for backward compatibility)
SCOPED_SCRAPES_DIR = OUTPUT_ROOT / "scrapes"
DIFF_EXPORTS_DIR = OUTPUT_ROOT / "diffs"
PERSISTENCE_DIR = OUTPUT_ROOT / "persistence"


def slug_for_path(s: str) -> str:
    """Convert string to filesystem-safe slug.
    
    Example: "Engine Rebuild Kits" -> "engine_rebuild_kits"
    """
    s = str(s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    return s or "unknown"


def get_sku_folder(category: str, manufacturer: str, sku: str) -> Path:
    """Return path for per-SKU folder: output/<Category>/<Manufacturer>/<SKU>/"""
    cat = slug_for_path(category)
    man = slug_for_path(manufacturer)
    sku_clean = slug_for_path(sku) or "unknown_sku"
    return OUTPUT_ROOT / cat / man / sku_clean


def get_sku_images_folder(category: str, manufacturer: str, sku: str) -> Path:
    """Return path for per-SKU images folder: output/<Category>/<Manufacturer>/<SKU>/images/"""
    return get_sku_folder(category, manufacturer, sku) / "images"
