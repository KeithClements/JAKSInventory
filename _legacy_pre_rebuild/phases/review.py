from __future__ import annotations

from typing import Iterable

from models import Product, resolve_selling_price
from engine.product_engine import ProductEngine


def _promote_stage(prod: Product, stage: int) -> None:
    pr = prod.pricing if isinstance(prod.pricing, dict) else {}
    prod.pricing = pr
    try:
        cur = int(pr.get("stage") or 1)
    except Exception:
        cur = 1
    if stage > cur:
        pr["stage"] = int(stage)


def review_pricing(
    rows: Iterable[Product],
    *,
    markup_pct: float = 30.0,
    include_core: bool = False,
    min_margin_warn_pct: float = 30.0,
) -> list[Product]:
    """Phase 4: compute validation flags + margin warnings; promote to Stage 4."""

    engine = ProductEngine()
    products = [p for p in rows if isinstance(p, Product)]

    for p in products:
        # Engine validation flags
        try:
            validation = engine.validate_product(p)
            flags = list(validation.flags or [])
        except Exception:
            flags = []

        # Margin warning (UI-only)
        try:
            cost, sell, _src = resolve_selling_price(p, markup_pct=float(markup_pct), include_core=bool(include_core))
            if float(cost) > 0 and float(sell) > 0:
                margin_pct = ((float(sell) - float(cost)) / float(sell)) * 100.0
                if margin_pct < float(min_margin_warn_pct):
                    flags.append(f"LOW_MARGIN_{margin_pct:.0f}%")
        except Exception:
            pass

        # Persist flags to product
        try:
            p.flags = list(dict.fromkeys([str(x) for x in (p.flags or []) + flags if str(x).strip()]))
        except Exception:
            p.flags = list(dict.fromkeys([str(x) for x in flags if str(x).strip()]))

        _promote_stage(p, 4)

    return products
