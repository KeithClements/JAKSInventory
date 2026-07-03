"""Product engine normalization/parsing.

This module is a shared home for any logic that derives an engine value from:
- scraped fields (e.g., `prod.engine`)
- URL/title heuristics
- future PAI-specific metadata

Design goals:
- Keep parsing logic deterministic and unit-testable
- Avoid UI dependencies
- Be safe on partial/dirty inputs
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .product_validation import ProductValidation, validate_product as _validate_product
from .template_inference import (
    infer_product_type as _infer_product_type,
    infer_template_suffix as _infer_template_suffix,
)


@dataclass(frozen=True)
class EngineInfo:
    """Derived engine information.

    `raw` is the original (best available) source string.
    `normalized` is a display/compare-friendly normalized form.
    `source` indicates where `raw` came from.
    """

    raw: str = ""
    normalized: str = ""
    source: str = ""  # e.g. "product.engine", "title", "manual"


@dataclass(frozen=True)
class ProductEngineConfig:
    """Configuration for ProductEngine.

    Keep this small; add flags/patterns only when needed.
    """

    collapse_whitespace: bool = True
    uppercase: bool = False
    margin_warn_pct: float | None = 15.0
    margin_critical_pct: float | None = 10.0
    min_abs_margin_warn: float | None = 75.0


class ProductEngine:
    """Engine resolver for Product-like objects.

    Expected product fields (best-effort):
    - engine: str
    - title: str
    """

    def __init__(self, config: ProductEngineConfig | None = None) -> None:
        self.config = config or ProductEngineConfig()

    def normalize(self, value: str | None) -> str:
        s = str(value or "").strip()
        if not s:
            return ""

        if self.config.collapse_whitespace:
            s = " ".join(s.split())

        # Normalize common separators (very conservative)
        s = re.sub(r"\s*[\/|]+\s*", " / ", s)
        s = re.sub(r"\s*-\s*", "-", s)

        if self.config.uppercase:
            s = s.upper()

        return s

    def extract(self, product: Any) -> EngineInfo:
        """Extract engine info from a product-like object.

        Current behavior:
        - Prefer `product.engine` when present/non-empty
        - Fall back to attempting to parse from `product.title`

        Parsing from title is intentionally minimal until you define rules.
        """

        raw_engine = str(getattr(product, "engine", "") or "").strip()
        if raw_engine:
            return EngineInfo(raw=raw_engine, normalized=self.normalize(raw_engine), source="product.engine")

        title = str(getattr(product, "title", "") or "").strip()
        from_title = self._extract_from_title(title)
        if from_title:
            return EngineInfo(raw=from_title, normalized=self.normalize(from_title), source="title")

        return EngineInfo(raw="", normalized="", source="")

    def validate_product(
        self,
        product: Any,
        *,
        markup_pct: float | None = None,
        include_core: bool = False,
        margin_warn_pct: float | None = None,
        margin_critical_pct: float | None = None,
        min_abs_margin_warn: float | None = None,
    ) -> ProductValidation:
        """Return a deterministic validation result for a product-like object."""
        return _validate_product(
            product,
            markup_pct=markup_pct,
            include_core=include_core,
            margin_warn_pct=self.config.margin_warn_pct if margin_warn_pct is None else margin_warn_pct,
            margin_critical_pct=(
                self.config.margin_critical_pct if margin_critical_pct is None else margin_critical_pct
            ),
            min_abs_margin_warn=(
                self.config.min_abs_margin_warn if min_abs_margin_warn is None else min_abs_margin_warn
            ),
        )

    def infer_product_type(self, title: str) -> str:
        return _infer_product_type(title)

    def infer_template_suffix(self, title: str, category: str, product_type: str | None = None) -> str | None:
        pt = product_type if product_type is not None else self.infer_product_type(title)
        return _infer_template_suffix(title, category, pt)

    def apply_to_product(self, product: Any, info: EngineInfo | None = None) -> EngineInfo:
        """Mutate the product object with normalized engine data (best-effort).

        If `info` is provided, this method will NOT call `extract()` again.
        This enables a single-call-site pattern where `extract()` is executed
        exactly once.
        """

        info = info if isinstance(info, EngineInfo) else self.extract(product)
        if info.normalized:
            try:
                setattr(product, "engine", info.normalized)
            except Exception:
                pass
        return info

    def _extract_from_title(self, title: str) -> str:
        """Very small heuristic extractor.

        Keep conservative: only pull an engine when it's explicitly called out.
        """

        if not title:
            return ""

        # Examples we can detect without lots of false positives:
        # "... for Cummins ISX ..." -> "Cummins ISX"
        m = re.search(r"\bfor\s+(Cummins|Caterpillar|Detroit)\s+([A-Z0-9.-]{2,})\b", title, re.IGNORECASE)
        if m:
            brand = m.group(1)
            code = m.group(2)
            return f"{brand} {code}".strip()

        return ""


# Convenience functions (kept for call-site ergonomics)
def normalize_engine_name(value: str | None) -> str:
    return ProductEngine().normalize(value)


def extract_engine_info(*, engine: str | None) -> EngineInfo:
    return ProductEngine().extract(type("_Tmp", (), {"engine": engine, "title": ""})())
