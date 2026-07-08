"""Deterministic product validation (engine layer).

This is intentionally UI-free so it can be reused for:
- GUI validation/status
- headless validation runs
- uploader preflight gating

The UI should only *render* the results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from models import ErrorCode, resolve_cost, resolve_selling_price
from .template_inference import infer_product_type, infer_template_suffix


Status = Literal["ready", "warning", "blocked"]


@dataclass(frozen=True)
class ProductValidation:
    flags: list[str]
    status: Status
    is_unknown_part: bool

    @property
    def is_blocked(self) -> bool:
        return self.status == "blocked"


def _is_unknown_part(prod: Any) -> bool:
    if bool(getattr(prod, "unknown_part", False)):
        return True
    try:
        pricing = getattr(prod, "pricing", None)
        if isinstance(pricing, dict) and bool(pricing.get("part_needs_review", False)):
            return True
    except Exception:
        pass
    sku_u = str(getattr(prod, "sku", "") or "").strip().upper()
    handle_u = str(getattr(prod, "handle", "") or "").strip().upper()
    return "UNKNOWN_PART" in sku_u or "UNKNOWN_PART" in handle_u


DEFAULT_MARGIN_WARN_PCT = 15.0
DEFAULT_MARGIN_CRITICAL_PCT = 10.0
DEFAULT_MIN_ABS_MARGIN_WARN = 75.0


def validate_product(
    prod: Any,
    *,
    markup_pct: float | None = None,
    include_core: bool = False,
    margin_warn_pct: float | None = DEFAULT_MARGIN_WARN_PCT,
    margin_critical_pct: float | None = DEFAULT_MARGIN_CRITICAL_PCT,
    min_abs_margin_warn: float | None = DEFAULT_MIN_ABS_MARGIN_WARN,
) -> ProductValidation:
    """Validate a product-like object.

    Mirrors previous UI logic:
    - Produces a list of string flags
    - Computes a single status: ready | warning | blocked
    """

    flags: list[str] = []

    title = str(getattr(prod, "title", "") or "").strip()
    if not title:
        flags.append(ErrorCode.ERROR_NO_TITLE.value)

    sku = str(getattr(prod, "sku", "") or "").strip()
    handle = str(getattr(prod, "handle", "") or "").strip()
    sku_u = sku.upper()
    handle_u = handle.upper()

    if not sku:
        flags.append(ErrorCode.ERROR_BAD_SKU.value)
    if "UNKNOWN_PART" in sku_u or "UNKNOWN_PART" in handle_u:
        flags.append(ErrorCode.ERROR_BAD_SKU.value)
    if bool(getattr(prod, "unknown_part", False)) and ErrorCode.ERROR_BAD_SKU.value not in flags:
        flags.append(ErrorCode.ERROR_BAD_SKU.value)

    pricing = getattr(prod, "pricing", {}) or {}

    try:
        if isinstance(pricing, dict) and bool(pricing.get("part_needs_review", False)):
            if ErrorCode.ERROR_BAD_SKU.value not in flags:
                flags.append(ErrorCode.ERROR_BAD_SKU.value)
    except Exception:
        pass

    # Pricing resolver stage:
    # - Resolved cost must be positive
    # - Final selling price (the value we'd upload) must be positive
    try:
        resolved_cost, _src = resolve_cost(prod)
    except Exception:
        resolved_cost, _src = 0.0, None

    selling: float
    if markup_pct is None:
        try:
            selling = float((pricing or {}).get("price", 0) or 0)
        except Exception:
            selling = 0.0
    else:
        try:
            _resolved_cost2, selling, _source = resolve_selling_price(
                prod,
                markup_pct=float(markup_pct),
                include_core=bool(include_core),
            )
            resolved_cost = float(_resolved_cost2)
        except Exception:
            selling = 0.0

    if float(resolved_cost) <= 0:
        flags.append(ErrorCode.ERROR_NO_COST.value)
    if float(selling) <= 0:
        flags.append(ErrorCode.ERROR_NO_PRICE.value)

    # Margin warning checks
    try:
        warn_pct = float(margin_warn_pct) if margin_warn_pct is not None else None
    except Exception:
        warn_pct = None
    try:
        critical_pct = float(margin_critical_pct) if margin_critical_pct is not None else None
    except Exception:
        critical_pct = None
    try:
        min_abs_warn = float(min_abs_margin_warn) if min_abs_margin_warn is not None else None
    except Exception:
        min_abs_warn = None

    margin_pct: float | None = None
    abs_margin: float | None = None
    if warn_pct is not None and float(resolved_cost) > 0 and float(selling) > 0:
        margin_pct = (float(selling) - float(resolved_cost)) / float(selling) * 100.0
    if float(resolved_cost) > 0 and float(selling) > 0:
        abs_margin = float(selling) - float(resolved_cost)

    if critical_pct is not None and margin_pct is not None and float(margin_pct) < float(critical_pct):
        flags.append("LOW_MARGIN_CRITICAL")
    if warn_pct is not None and margin_pct is not None and float(margin_pct) < float(warn_pct):
        flags.append("LOW_MARGIN")
    if min_abs_warn is not None and abs_margin is not None and float(abs_margin) < float(min_abs_warn):
        flags.append("LOW_ABSOLUTE_MARGIN")

    try:
        img_count = int(getattr(prod, "image_count_raw", 0) or 0)
    except Exception:
        img_count = 0
    if img_count <= 0:
        flags.append(ErrorCode.ERROR_NO_IMAGES.value)

    # Upload targeting (URL scrapes may not have a category)
    category = str(getattr(prod, "category", "") or "").strip()
    if not category:
        flags.append("MISSING_CATEGORY")
    else:
        # If a UI/template hint exists (e.g., derived from kit contents/xrefs),
        # treat it as a sufficient non-blocking fallback for template targeting.
        try:
            tmpl_hint = str(getattr(prod, "template", "") or "").strip()
        except Exception:
            tmpl_hint = ""
        if tmpl_hint and tmpl_hint.upper() != "N/A":
            tmpl_hint = tmpl_hint
        else:
            tmpl_hint = ""

        if tmpl_hint:
            template_suffix = "UI_HINT"
        else:
            template_suffix = None

        # Only warn about template when we have enough context to infer one.
        if template_suffix is None:
            try:
                product_type = infer_product_type(title)
                template_suffix = infer_template_suffix(title, category, product_type)
            except Exception:
                template_suffix = None
        if not template_suffix:
            flags.append("MISSING_TEMPLATE")

    blocked_set = {
        ErrorCode.ERROR_BAD_SKU.value,
        ErrorCode.ERROR_NO_TITLE.value,
        ErrorCode.ERROR_NO_PRICE.value,
    }
    warning_set = {
        ErrorCode.ERROR_NO_IMAGES.value,
        ErrorCode.ERROR_NO_COST.value,
        "MISSING_CATEGORY",
        "MISSING_TEMPLATE",
    }

    unknown = _is_unknown_part(prod)
    has_low_margin = any(str(f).startswith("LOW_MARGIN") for f in flags)
    has_low_abs_margin = "LOW_ABSOLUTE_MARGIN" in flags
    # Phase 2.1 stabilization: margin is WARNING-only; never hard-block.
    if unknown or any(f in blocked_set for f in flags):
        status: Status = "blocked"
    elif has_low_margin or has_low_abs_margin or any(f in warning_set for f in flags):
        status = "warning"
    else:
        status = "ready"

    return ProductValidation(flags=flags, status=status, is_unknown_part=unknown)


# ---------------------------------------------------------------------------
# P1.4 — DB-product validation
#
# ``validate_product`` above operates on the scraper-side ``models.Product``.
# The GUI works with flat DB-shape dicts (one row of ``products``), which
# have different keys (``cost``, ``selling_price``, ``core_charge``,
# warranty columns, etc.). ``validate_db_product`` mirrors the same
# contract but for DB dicts.
# ---------------------------------------------------------------------------

# Flag constants (string codes) used by validate_db_product.
DB_FLAG_MISSING_TITLE = "missing_title"
DB_FLAG_MISSING_SKU = "missing_sku"
DB_FLAG_MISSING_COST = "missing_cost"
DB_FLAG_MISSING_PRICE = "missing_price"
DB_FLAG_NEGATIVE_MARGIN = "negative_margin"
DB_FLAG_LOW_MARGIN = "low_margin"
DB_FLAG_CRITICAL_MARGIN = "critical_margin"
DB_FLAG_CORE_REQUIRED = "core_required"
DB_FLAG_WARRANTY_INCONSISTENT = "warranty_inconsistent"
DB_FLAG_INVALID_WARRANTY_UNIT = "invalid_warranty_unit"

# Category names that typically carry a core charge. Match is case-insensitive
# and checks both the leaf category and any parent path segment.
CORE_ELIGIBLE_CATEGORIES: frozenset[str] = frozenset({
    "starters",
    "alternators",
    "turbochargers",
    "turbos",
    "injectors",
    "fuel pumps",
    "fuel injection pumps",
    "water pumps",
    "air compressors",
    "compressors",
    "power steering pumps",
    "ac compressors",
    "cylinder heads",
    "engines",
    "long blocks",
    "short blocks",
})

# Valid warranty period units.
_VALID_WARRANTY_UNITS: frozenset[str] = frozenset({"days", "months", "years"})


def _f(prod: dict, *keys, default: float = 0.0) -> float:
    for k in keys:
        v = prod.get(k) if isinstance(prod, dict) else None
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return default


def _is_core_eligible(category: str | None) -> bool:
    if not category:
        return False
    cat = str(category).strip().lower()
    if not cat:
        return False
    # Match leaf or any path segment (handles "Engine Parts > Starters").
    for seg in (s.strip() for s in cat.replace("|", ">").split(">")):
        if seg in CORE_ELIGIBLE_CATEGORIES:
            return True
    return cat in CORE_ELIGIBLE_CATEGORIES


def validate_db_product(
    prod: dict,
    *,
    margin_warn_pct: float = DEFAULT_MARGIN_WARN_PCT,
    margin_critical_pct: float = DEFAULT_MARGIN_CRITICAL_PCT,
    core_eligible_categories: frozenset[str] | None = None,
) -> ProductValidation:
    """Validate a products-table row (dict).

    Returns a ``ProductValidation`` with the same shape used by the
    scraper-side validator so both pipelines can share UI rendering.
    """
    if not isinstance(prod, dict):
        prod = dict(prod) if prod else {}

    flags: list[str] = []
    blocked = False

    title = str(prod.get("title") or "").strip()
    sku = str(prod.get("sku") or "").strip()
    if not title:
        flags.append(DB_FLAG_MISSING_TITLE)
        blocked = True
    if not sku:
        flags.append(DB_FLAG_MISSING_SKU)
        blocked = True

    cost = _f(prod, "cost", "unit_cost", "last_cost")
    price = _f(prod, "selling_price", "price", "unit_price")
    core = _f(prod, "core_charge")

    if price <= 0:
        flags.append(DB_FLAG_MISSING_PRICE)
        blocked = True
    if cost <= 0:
        # Warning only — allow zero-cost products (e.g. promotional) but
        # surface them.
        flags.append(DB_FLAG_MISSING_COST)

    # Margin checks (only meaningful when both cost and price are set).
    has_low_margin = False
    has_critical_margin = False
    has_negative_margin = False
    if price > 0 and cost > 0:
        margin_pct = (price - cost) / price * 100.0
        if margin_pct < 0:
            has_negative_margin = True
            flags.append(DB_FLAG_NEGATIVE_MARGIN)
            blocked = True
        elif margin_pct < float(margin_critical_pct or 0):
            has_critical_margin = True
            flags.append(DB_FLAG_CRITICAL_MARGIN)
        elif margin_pct < float(margin_warn_pct or 0):
            has_low_margin = True
            flags.append(DB_FLAG_LOW_MARGIN)

    # Warranty consistency.
    # Schema columns (when present): supplier_warranty_enabled,
    # supplier_warranty_days|_months|_years|_period + _unit,
    # jaks_warranty_enabled + jaks_warranty_period + _unit.
    for vendor in ("supplier", "jaks"):
        enabled = bool(prod.get(f"{vendor}_warranty_enabled"))
        period = prod.get(f"{vendor}_warranty_period")
        unit = prod.get(f"{vendor}_warranty_unit")
        if not enabled:
            continue
        try:
            period_val = float(period) if period not in (None, "") else 0.0
        except (TypeError, ValueError):
            period_val = 0.0
        if period_val <= 0:
            flags.append(DB_FLAG_WARRANTY_INCONSISTENT)
        if unit and str(unit).strip().lower() not in _VALID_WARRANTY_UNITS:
            flags.append(DB_FLAG_INVALID_WARRANTY_UNIT)

    # Core charge required for eligible categories unless explicitly opted out.
    eligible_set = core_eligible_categories or CORE_ELIGIBLE_CATEGORIES
    category = prod.get("category") or prod.get("product_type")
    cat_lower = str(category or "").strip().lower()
    opted_out = bool(prod.get("no_core_charge") or prod.get("core_exempt"))
    is_core_cat = False
    if cat_lower:
        for seg in (s.strip() for s in cat_lower.replace("|", ">").split(">")):
            if seg in eligible_set:
                is_core_cat = True
                break
    if is_core_cat and not opted_out and core <= 0:
        flags.append(DB_FLAG_CORE_REQUIRED)

    if blocked:
        status: Status = "blocked"
    elif flags:
        status = "warning"
    else:
        status = "ready"

    unknown = False
    try:
        sku_u = sku.upper()
        unknown = "UNKNOWN_PART" in sku_u or bool(prod.get("unknown_part"))
    except Exception:
        pass

    return ProductValidation(
        flags=list(dict.fromkeys(flags)),  # de-dupe while preserving order
        status=status,
        is_unknown_part=unknown,
    )
