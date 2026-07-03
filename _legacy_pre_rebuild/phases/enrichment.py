from __future__ import annotations

from typing import Any, Iterable

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


def enrich_pai(rows: Iterable[Product], *, pai_client: Any) -> list[Product]:
    """Phase 3: advisory PAI enrichment + promote to Stage 3.

    Uses existing PAI enrichment helpers (no SKU/supplier mutations beyond what
    your current code already does when you choose to apply them).
    """

    from sources.pai_enrichment import enrich_pai_advisory_many_parallel

    products = [p for p in rows if isinstance(p, Product)]
    if not products:
        return []

    # Advisory only (writes into `pai_advisory`).
    enrich_pai_advisory_many_parallel(products, pai_client)

    for p in products:
        _promote_stage(p, 3)

    return products
