from __future__ import annotations

from pathlib import Path
from typing import Any

from models import Product


def _promote_stage(prod: Product, stage: int) -> None:
    pr = prod.pricing if isinstance(prod.pricing, dict) else {}
    prod.pricing = pr
    try:
        cur = int(pr.get("stage") or 1)
    except Exception:
        cur = 1
    if stage > cur:
        pr["stage"] = int(stage)


def upload_shopify(
    products_dict: dict[str, Product],
    images_dir: Path,
    *,
    publish: bool,
    markup_pct: float,
    include_core: bool,
    signals: Any | None = None,
) -> dict[str, Any]:
    """Phase 5: upload to Shopify + promote uploaded items to Stage 5."""

    from phases.uploader import upload_to_shopify

    result = upload_to_shopify(
        products_dict,
        images_dir,
        publish=publish,
        markup_pct=markup_pct,
        include_core=include_core,
        signals=signals,
    )

    # Best-effort stage promotion for all attempted items.
    for p in (products_dict or {}).values():
        try:
            _promote_stage(p, 5)
        except Exception:
            pass

    return {"result": result}
