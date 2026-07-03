"""Product UI entry normalization.

This module defines the single entry point that must be used for ALL products
entering the UI.

Rules:
- Calls `engine.extract()` exactly once
- Applies mutation in one place
- Returns `(product, info)` so callers can log/debug if desired
"""

from __future__ import annotations

from typing import Any, Tuple

from engine.product_engine import EngineInfo, ProductEngine


def normalize_product_for_ui(engine: ProductEngine, product: Any) -> Tuple[Any, EngineInfo]:
    """Single entry point for engine normalization.

    Must be called for ALL products entering the UI.
    """

    info = engine.extract(product)

    # Best-effort mutation (explicit and visible)
    engine.apply_to_product(product, info)

    # Marker used for central safety assertions.
    # Kept in product.__dict__ but filtered out from JSON saves by the UI layer.
    try:
        setattr(product, "_ui_normalized", True)
        setattr(product, "_ui_engine_source", str(getattr(info, "source", "") or ""))
    except Exception:
        pass

    return product, info
