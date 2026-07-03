"""Part Search Service — multi-source unified search.

Searches in order:
  1. Internal inventory (products, part_numbers)
  2. Internal cross references / interchanges
  3. PAI lookup
  4. SAMPA lookup (placeholder)

Results are grouped by source for the UI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from db import (
    search_by_part_number,
    search_interchanges,
    get_product_by_sku,
    get_product_by_id,
    get_all_products,
)
from db.database import get_db

log = logging.getLogger(__name__)


class SearchSource(Enum):
    INTERNAL = "Internal Inventory"
    XREF = "Cross Reference"
    PAI = "PAI"
    SAMPA = "SAMPA"


@dataclass
class SearchResult:
    source: SearchSource
    product_id: Optional[int] = None
    sku: str = ""
    title: str = ""
    description: str = ""
    qty_on_hand: int = 0
    cost: float = 0.0
    price: float = 0.0
    vendor: str = ""
    vendor_part_number: str = ""
    oem_part_number: str = ""
    matched_number: str = ""
    match_type: str = ""
    manufacturer: str = ""
    category: str = ""
    is_internal: bool = False
    raw: dict = field(default_factory=dict)


class PartSearchService:
    """Unified part search across all sources."""

    def search(
        self,
        query: str,
        *,
        search_internal: bool = True,
        search_xref: bool = True,
        search_pai: bool = True,
        search_sampa: bool = True,
    ) -> dict[SearchSource, list[SearchResult]]:
        """Run multi-source search and return grouped results."""
        if not query or not query.strip():
            return {}

        query = query.strip()
        results: dict[SearchSource, list[SearchResult]] = {}

        if search_internal:
            internal = self._search_internal(query)
            if internal:
                results[SearchSource.INTERNAL] = internal

        if search_xref:
            xref = self._search_xref(query)
            if xref:
                results[SearchSource.XREF] = xref

        if search_pai:
            pai = self._search_pai(query)
            if pai:
                results[SearchSource.PAI] = pai

        if search_sampa:
            sampa = self._search_sampa(query)
            if sampa:
                results[SearchSource.SAMPA] = sampa

        return results

    # ------------------------------------------------------------------
    # Internal inventory
    # ------------------------------------------------------------------
    def _search_internal(self, query: str) -> list[SearchResult]:
        seen_ids: set[int] = set()
        results: list[SearchResult] = []

        # Direct SKU lookup
        prod = get_product_by_sku(query)
        if not prod and not query.upper().startswith("JAKS-"):
            prod = get_product_by_sku(f"JAKS-{query}")
        if prod:
            results.append(self._product_to_result(prod, "exact_sku"))
            seen_ids.add(prod["id"])

        # Part number search
        for row in search_by_part_number(query, limit=20):
            pid = row.get("id")
            if pid and pid not in seen_ids:
                results.append(self._product_to_result(
                    row, row.get("match_type", "part_number"),
                    matched=row.get("matched_part", ""),
                ))
                seen_ids.add(pid)

        # Keyword / title search
        with get_db() as conn:
            q = f"%{query}%"
            rows = conn.execute(
                "SELECT * FROM products WHERE title LIKE %s OR description_html LIKE %s LIMIT 10",
                (q, q),
            ).fetchall()
            for row in rows:
                r = dict(row) if hasattr(row, "keys") else {}
                pid = r.get("id")
                if pid and pid not in seen_ids:
                    results.append(self._product_to_result(r, "keyword"))
                    seen_ids.add(pid)

        return results

    # ------------------------------------------------------------------
    # Cross references / interchanges
    # ------------------------------------------------------------------
    def _search_xref(self, query: str) -> list[SearchResult]:
        seen_ids: set[int] = set()
        results: list[SearchResult] = []

        # search_interchanges already joins part_interchanges → products
        for row in search_interchanges(query, limit=20):
            pid = row.get("product_id") or row.get("id")
            if pid and pid not in seen_ids:
                results.append(SearchResult(
                    source=SearchSource.XREF,
                    product_id=pid,
                    sku=row.get("sku", ""),
                    title=row.get("title", ""),
                    qty_on_hand=int(row.get("qty_on_hand") or 0),
                    cost=float(row.get("cost") or 0),
                    price=float(row.get("selling_price") or 0),
                    matched_number=row.get("interchange_number", ""),
                    match_type=row.get("interchange_type", ""),
                    manufacturer=row.get("product_manufacturer", "") or row.get("manufacturer", ""),
                    category=row.get("category", ""),
                    is_internal=True,
                    raw=row,
                ))
                seen_ids.add(pid)

        # Also search xref_chains
        try:
            from db.xref_chain_service import search_aftermarket_by_oem
            oem_result = search_aftermarket_by_oem(query)
            if oem_result and oem_result.aftermarket_options:
                for opt in oem_result.aftermarket_options:
                    am_sku = f"JAKS-{opt.aftermarket_part_number}" if opt.aftermarket_part_number else ""
                    prod = get_product_by_sku(am_sku) if am_sku else None
                    pid = prod["id"] if prod else None
                    if pid and pid in seen_ids:
                        continue
                    results.append(SearchResult(
                        source=SearchSource.XREF,
                        product_id=pid,
                        sku=prod.get("sku", am_sku) if prod else am_sku,
                        title=prod.get("title", "") if prod else opt.aftermarket_manufacturer,
                        qty_on_hand=int(prod.get("qty_on_hand", 0)) if prod else 0,
                        cost=float(prod.get("cost", 0)) if prod else 0,
                        price=float(prod.get("selling_price", 0)) if prod else 0,
                        matched_number=opt.aftermarket_part_number,
                        match_type="xref_chain",
                        manufacturer=opt.aftermarket_manufacturer,
                        is_internal=bool(prod),
                        raw={"oem": query, "aftermarket": opt.aftermarket_part_number},
                    ))
                    if pid:
                        seen_ids.add(pid)
        except Exception:
            pass

        return results

    # ------------------------------------------------------------------
    # PAI vendor lookup
    # ------------------------------------------------------------------
    def _search_pai(self, query: str) -> list[SearchResult]:
        """Search PAI portal for vendor pricing and cross-references.

        Returns results only if PAI is enabled and a session is available.
        """
        try:
            import os
            if not os.environ.get("PAI_ENABLE", "").strip() == "1":
                return []

            from sources.pai_enrichment import enrich_pai_advisory
            # enrich_pai_advisory expects a product dict with pricing info
            # We build a minimal one to query PAI
            result = enrich_pai_advisory({
                "sku": f"JAKS-{query}",
                "oem_part_number": query,
                "pricing": {"cost": 0, "selling_price": 0},
            })
            if not result:
                return []

            pai_results = []
            if isinstance(result, dict):
                pai_cost = result.get("pai_cost") or result.get("cost")
                pai_sku = result.get("pai_sku", "")
                if pai_cost or pai_sku:
                    pai_results.append(SearchResult(
                        source=SearchSource.PAI,
                        vendor_part_number=pai_sku,
                        oem_part_number=query,
                        cost=float(pai_cost or 0),
                        vendor="PAI",
                        title=result.get("description", f"PAI: {query}"),
                        match_type="pai_lookup",
                        is_internal=False,
                        raw=result,
                    ))
            return pai_results
        except Exception as exc:
            log.debug("PAI search failed for %s: %s", query, exc)
            return []

    # ------------------------------------------------------------------
    # SAMPA vendor lookup (placeholder)
    # ------------------------------------------------------------------
    def _search_sampa(self, query: str) -> list[SearchResult]:
        """Search SAMPA vendor catalog.

        Placeholder for future SAMPA integration.
        """
        # TODO: Implement SAMPA API integration when available
        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _product_to_result(
        prod: dict,
        match_type: str,
        matched: str = "",
    ) -> SearchResult:
        return SearchResult(
            source=SearchSource.INTERNAL,
            product_id=prod.get("id"),
            sku=prod.get("sku", ""),
            title=prod.get("title", ""),
            description=prod.get("description_html", ""),
            qty_on_hand=int(prod.get("qty_on_hand") or 0),
            cost=float(prod.get("cost") or prod.get("pai_cost") or 0),
            price=float(prod.get("selling_price") or 0),
            vendor=prod.get("supplier", ""),
            vendor_part_number=prod.get("pai_sku", ""),
            oem_part_number=prod.get("oem_part_number", ""),
            matched_number=matched,
            match_type=match_type,
            manufacturer=prod.get("manufacturer", ""),
            category=prod.get("category", ""),
            is_internal=True,
            raw=prod,
        )
