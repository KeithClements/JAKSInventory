"""Match Engine — duplicate detection and intelligent part matching.

Before creating any new product, the system must determine:
- Do we already have this part?
- Is this OEM number already stored?
- Is this vendor part number already stored?
- Is this cross reference already linked to an existing SKU?

Match Outcomes:
  A — Existing product found (high confidence)
  B — Multiple possible matches (user must choose)
  C — No match (proceed with import/creation)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from db import (
    get_product_by_sku,
    search_by_part_number,
    search_interchanges,
    find_similar_products,
    get_product_by_id,
)
from db.database import get_db


class MatchOutcome(Enum):
    EXACT = "exact"          # Single definitive match
    MULTIPLE = "multiple"    # Several possible matches
    NONE = "none"            # No match found


@dataclass
class MatchResult:
    outcome: MatchOutcome
    matches: list[dict] = field(default_factory=list)
    best_match: Optional[dict] = None
    match_reason: str = ""

    @property
    def has_match(self) -> bool:
        return self.outcome != MatchOutcome.NONE


def _normalize(part: str) -> str:
    """Strip whitespace, hyphens, spaces; uppercase for comparison."""
    if not part:
        return ""
    return re.sub(r"[\s\-_./]+", "", part.strip().upper())


class MatchEngine:
    """Reusable match engine used by Part Finder, Vendor Import, and ESN Lookup."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def check_duplicate(
        self,
        *,
        sku: str = "",
        oem_part_number: str = "",
        vendor_part_number: str = "",
        xref_part_number: str = "",
        title: str = "",
        exclude_id: int | None = None,
    ) -> MatchResult:
        """Run all matching rules and return the match outcome.

        Check order (strongest → weakest):
          1. Exact SKU
          2. Exact vendor part number (in SKU or pai_sku)
          3. Exact OEM part number
          4. Exact cross reference
          5. Normalized match (trim/case/special chars)
          6. Title word similarity
        """
        all_matches: list[dict] = []
        seen_ids: set[int] = set()

        def _add(rows: list[dict], reason: str, confidence: str = "high"):
            for r in rows:
                pid = r.get("id")
                if pid and pid not in seen_ids:
                    if exclude_id and pid == exclude_id:
                        continue
                    r["match_reason"] = reason
                    r["confidence"] = confidence
                    all_matches.append(r)
                    seen_ids.add(pid)

        # 1. Exact SKU
        if sku:
            prod = get_product_by_sku(sku)
            if prod and (not exclude_id or prod["id"] != exclude_id):
                _add([prod], "exact_sku")

        # 2‒5 use DB searches
        with get_db() as conn:
            # 2. Vendor part number match (check sku and pai_sku columns)
            if vendor_part_number:
                norm_vp = _normalize(vendor_part_number)
                rows = conn.execute(
                    "SELECT * FROM products WHERE UPPER(REPLACE(REPLACE(sku,'-',''),' ','')) LIKE %s "
                    "OR UPPER(REPLACE(REPLACE(COALESCE(pai_sku,''),'-',''),' ','')) LIKE %s LIMIT 10",
                    (f"%{norm_vp}%", f"%{norm_vp}%"),
                ).fetchall()
                _add([dict(r) for r in rows], "vendor_part")

            # 3. OEM part number match
            if oem_part_number:
                norm_oem = _normalize(oem_part_number)
                # Products table columns
                rows = conn.execute(
                    "SELECT * FROM products WHERE UPPER(REPLACE(REPLACE(COALESCE(oem_part_number,''),'-',''),' ','')) = %s LIMIT 10",
                    (norm_oem,),
                ).fetchall()
                _add([dict(r) for r in rows], "exact_oem")

                # Part numbers table
                rows = conn.execute(
                    "SELECT DISTINCT p.* FROM products p "
                    "JOIN part_numbers pn ON p.id = pn.product_id "
                    "WHERE pn.number_normalized = %s LIMIT 10",
                    (norm_oem,),
                ).fetchall()
                _add([dict(r) for r in rows], "part_number_table")

            # 4. Cross reference match
            if xref_part_number:
                norm_xr = _normalize(xref_part_number)
                rows = conn.execute(
                    "SELECT DISTINCT p.* FROM products p "
                    "JOIN part_interchanges pi ON p.id = pi.product_id "
                    "WHERE UPPER(REPLACE(REPLACE(pi.interchange_number,'-',''),' ','')) = %s LIMIT 10",
                    (norm_xr,),
                ).fetchall()
                _add([dict(r) for r in rows], "xref_interchange", "high")

                # Also check xref_chains table
                rows = conn.execute(
                    "SELECT DISTINCT p.* FROM products p "
                    "WHERE UPPER(REPLACE(REPLACE(COALESCE(p.xref_part_number,''),'-',''),' ','')) = %s LIMIT 10",
                    (norm_xr,),
                ).fetchall()
                _add([dict(r) for r in rows], "xref_column", "medium")

        # 6. Title similarity (lowest weight)
        if title and not all_matches:
            similar = find_similar_products(
                title=title, sku=sku, oem_part_number=oem_part_number,
                xref_part_number=xref_part_number, exclude_id=exclude_id,
            )
            _add(similar, "title_similarity", "low")

        # Determine outcome
        if not all_matches:
            return MatchResult(outcome=MatchOutcome.NONE)

        high = [m for m in all_matches if m.get("confidence") == "high"]
        if len(high) == 1:
            return MatchResult(
                outcome=MatchOutcome.EXACT,
                matches=all_matches,
                best_match=high[0],
                match_reason=high[0].get("match_reason", ""),
            )

        return MatchResult(
            outcome=MatchOutcome.MULTIPLE if len(all_matches) > 1 else MatchOutcome.EXACT,
            matches=all_matches,
            best_match=all_matches[0],
            match_reason=all_matches[0].get("match_reason", ""),
        )

    def find_by_any_identifier(self, query: str, limit: int = 20) -> list[dict]:
        """Search across all identifier types for a given query string.

        Returns deduplicated product list with match info.
        """
        if not query or not query.strip():
            return []

        results: list[dict] = []
        seen_ids: set[int] = set()

        def _add(rows, source):
            for r in rows:
                pid = r.get("id") or r.get("product_id")
                if pid and pid not in seen_ids:
                    r["_match_source"] = source
                    results.append(r)
                    seen_ids.add(pid)

        # Direct product search (SKU, title, keywords)
        _add(search_by_part_number(query, limit=limit), "part_number")

        # Interchange search
        _add(search_interchanges(query, limit=limit), "interchange")

        # Plain SKU lookup
        prod = get_product_by_sku(query.strip())
        if prod:
            _add([prod], "exact_sku")

        # JAKS- prefixed lookup
        if not query.strip().upper().startswith("JAKS-"):
            prod = get_product_by_sku(f"JAKS-{query.strip()}")
            if prod:
                _add([prod], "sku_prefix")

        return results[:limit]
