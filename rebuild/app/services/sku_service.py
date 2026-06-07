"""
app/services/sku_service.py
===========================
JAKS customer-facing SKU generator (owner-locked scheme, 2026-06-06).

    JAKS-[ENGINE]-[CATEGORY]-[V][NNNN]

  • ENGINE   — short code for the engine platform (ISX, DT466, C15, …). OMITTED
               for multi-fit / engine-agnostic parts → JAKS-[CATEGORY]-[V][NNNN].
  • CATEGORY — short code of the DEEPEST category a product is tagged to.
  • V        — 1-digit OPAQUE vendor number set on the vendor record (PAI=9, …).
               A customer cannot tell which supplier "9" is, and the sequence is a
               JAKS counter (NOT the vendor's real part #), so the SKU never leaks
               the source. The readable vendor part number stays on
               ProductVendorSource and OFF customer documents.
  • NNNN     — 4-digit zero-padded part sequence, unique per (engine, category).

Key rules (owner, 2026-06-06):
  • Vendor-INDEPENDENT: the vendor identity is structured data, never the basis of
    the SKU; only the opaque 1-digit V appears.
  • FROZEN: a product's engine_code / category_code / part_seq are stamped at mint
    time so the assembled sku never drifts if a category is later renamed/re-coded.
  • ONE SKU = ONE VENDOR: a part sourced from a 2nd vendor gets its OWN sku that
    SHARES the part's sequence with a different vendor digit (readable equivalence:
    JAKS-ISX-INJ-90001  ↔  JAKS-ISX-INJ-30001). See ``assign_twin_sku``.
  • Cores stay on CoreCharge — there are no ``-CORE`` SKUs.

This module is pure/stateless at the function level (easily unit-tested) with a
thin DB-aware ``SkuService`` for sequence allocation and product mutation.
"""
from __future__ import annotations

import re

from sqlalchemy import func

from app.constants import ENGINE_MODELS_BY_MAKE
from app.models.product import Product, ProductCategory
from app.services.base import BaseService

SKU_PREFIX = "JAKS"
SEQ_WIDTH = 4
_GENERIC_CATEGORY_CODE = "GEN"

# Words that carry no signal in an engine-model or category string.
_ENGINE_NOISE = {"ENGINE", "ENGINES", "SERIES", "MODEL", "MODELS", "TRUCK", "THE", "FOR", "AND"}
_CATEGORY_NOISE = {
    "PARTS", "PART", "SYSTEM", "SYSTEMS", "ASSEMBLY", "ASSY", "COMPONENT", "COMPONENTS",
    "AND", "OF", "THE", "KIT", "KITS", "MISC", "GENERAL", "RELEASE",
}


# ── Code derivation (pure) ──────────────────────────────────────────────────────

def _alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# Canonical model codes from the engine picker (ENGINE_MODELS_BY_MAKE) → for
# preferring a clean, consistent code when the messy import string contains one.
_CANON_MODEL_CODES: list[str] = sorted(
    {
        _alnum(m)
        for models in ENGINE_MODELS_BY_MAKE.values()
        for m in models
        if m and m != "Other" and _alnum(m)
    },
    key=len,
    reverse=True,  # longest first so 'MAXXFORCE13' wins over 'MAXXFORCE'
)


def engine_code(engine_model: str) -> str:
    """Normalize a (possibly messy) engine-model string to a short SKU code.

    '' when there is no single engine (multi-fit / engine-agnostic) — the caller
    then omits the [ENGINE] segment. Examples:
        'ISX SERIES'          -> 'ISX'
        'DT466 ENGINE'        -> 'DT466'
        'MX-13 SERIES'        -> 'MX13'
        'E6 2  VALVE ENGINES' -> 'E6'
        ''                    -> ''
    """
    s = (engine_model or "").strip()
    if not s:
        return ""
    flat = _alnum(s)
    # Prefer a canonical engine-picker code if the string clearly contains one.
    for canon in _CANON_MODEL_CODES:
        if len(canon) >= 2 and canon in flat:
            return canon[:8]
    # Otherwise take the first meaningful token (model usually leads the string).
    for tok in re.split(r"[\s/]+", s.upper()):
        code = _alnum(tok)
        if code and tok not in _ENGINE_NOISE:
            return code[:8]
    return ""


def derive_category_code(name: str) -> str:
    """Deterministic fallback code from a category name, when no explicit
    ``code`` is set on the node. Coarse by design — the owner refines codes on
    the Category Maintenance screen. Examples:
        'ENGINE PARTS'    -> 'ENG'
        'TRANSMISSION'    -> 'TRAN'
        'AIR BRAKE PARTS' -> 'AIRB'
        'CAB'             -> 'CAB'
    """
    raw = re.sub(r"[^A-Za-z0-9 ]", " ", (name or "")).upper()
    words = [w for w in raw.split() if w and w not in _CATEGORY_NOISE]
    if not words:
        words = [w for w in raw.split() if w]
    if not words:
        return _GENERIC_CATEGORY_CODE
    if len(words) == 1:
        # Single word: ENGINE -> ENG, TRANSMISSION -> TRAN.
        return words[0][:3] if len(words[0]) <= 6 else words[0][:4]
    # Multiple significant words: first 3 of the first + initials of the rest.
    code = words[0][:3] + "".join(w[0] for w in words[1:3])
    return code[:6]


def assemble_sku(category_code: str, vendor_digit: str, part_seq: int,
                 engine_code: str = "") -> str:
    """Build the SKU string from its parts. Engine segment omitted when blank."""
    segs = [SKU_PREFIX]
    if engine_code:
        segs.append(engine_code)
    segs.append(category_code or _GENERIC_CATEGORY_CODE)
    return "-".join(segs) + f"-{vendor_digit}{int(part_seq):0{SEQ_WIDTH}d}"


# ── DB-aware service ────────────────────────────────────────────────────────────

class SkuService(BaseService):
    """Allocates sequences and stamps the frozen SKU components onto products."""

    # -- helpers ------------------------------------------------------------------
    def code_for_category(self, category: ProductCategory | None) -> str:
        """The category's explicit ``code`` if set, else a derived fallback.
        ``category`` is the product's tagged (deepest) node."""
        if category is None:
            return _GENERIC_CATEGORY_CODE
        if (category.code or "").strip():
            return category.code.strip().upper()
        return derive_category_code(category.name)

    def vendor_digit(self, vendor) -> str:
        """The opaque 1-digit vendor number, or '' if unset (caller must ensure
        it is set before minting a real SKU)."""
        return (getattr(vendor, "vendor_number", "") or "").strip()

    def next_part_seq(self, engine_code: str, category_code: str) -> int:
        """Next free sequence within an (engine_code, category_code) group. Reuses
        the max across all products in the group so vendor-twins (which share a
        sequence) never push the counter past the true distinct-part count."""
        # Sessions run autoflush=False — flush so part_seqs stamped earlier in this
        # transaction are visible to the aggregate (otherwise sequential mints in one
        # session all read max=NULL and collide). Bulk callers use an in-memory
        # counter instead (see scripts/backfill_sku_scheme.py).
        self.db.flush()
        mx = (
            self.db.query(func.max(Product.part_seq))
            .filter(Product.engine_code == engine_code,
                    Product.category_code == category_code)
            .scalar()
        )
        return (mx or 0) + 1

    # -- minting ------------------------------------------------------------------
    def assign_new_sku(self, product: Product, vendor) -> str:
        """Stamp a brand-new part with a fresh sequence and assemble its sku.
        Mutates ``product`` (engine_code/category_code/part_seq/sku). Caller commits."""
        ecode = engine_code(product.engine_model or "")
        ccode = self.code_for_category(product.category)
        seq = self.next_part_seq(ecode, ccode)
        digit = self.vendor_digit(vendor)
        product.engine_code = ecode
        product.category_code = ccode
        product.part_seq = seq
        product.sku = assemble_sku(ccode, digit, seq, engine_code=ecode)
        return product.sku

    def assign_twin_sku(self, new_product: Product, base_product: Product, vendor) -> str:
        """Mint ``new_product`` as another vendor's version of ``base_product``:
        it REUSES the base part's (engine_code, category_code, part_seq) and only
        swaps the vendor digit, so the two SKUs read as the same part from
        different suppliers (90001 ↔ 30001). Caller commits."""
        new_product.engine_code = base_product.engine_code
        new_product.category_code = base_product.category_code
        new_product.part_seq = base_product.part_seq
        digit = self.vendor_digit(vendor)
        new_product.sku = assemble_sku(
            base_product.category_code, digit, base_product.part_seq,
            engine_code=base_product.engine_code,
        )
        return new_product.sku

    # -- catalog backfill ---------------------------------------------------------
    def regenerate_catalog(self, *, apply: bool = False,
                           limit: int | None = None) -> dict:
        """Regenerate the customer-facing ``sku`` for every product from its §18
        classification (category + engine_model) and its preferred vendor's digit.

        DRY-RUN by default — computes the full plan + samples, writes nothing.
        On ``apply=True`` it writes in TWO PHASES (stage temp skus → final skus) so
        the UNIQUE(sku) constraint is never tripped mid-update, and parks each old
        ``JAKS-PAI-…`` value on the preferred vendor source's ``vendor_sku`` so the
        old number stays searchable. Mirrors scripts/backfill_s18_classification.py.

        Products with no preferred vendor source, or whose vendor has no
        ``vendor_number`` set, are SKIPPED (and counted) — they keep their old sku.
        """
        summary: dict = {
            "apply": apply, "products": 0, "regenerated": 0,
            "skipped_no_vendor": 0, "skipped_no_vendor_number": 0,
            "by_vendor_digit": {}, "samples": [],
        }
        q = self.db.query(Product).order_by(Product.id)
        if limit:
            q = q.limit(limit)
        products = q.all()
        summary["products"] = len(products)

        counters: dict[tuple[str, str], int] = {}
        plan: list[tuple] = []
        for p in products:
            src = p.preferred_vendor_source
            if src is None or src.vendor is None:
                summary["skipped_no_vendor"] += 1
                continue
            digit = (src.vendor.vendor_number or "").strip()
            if not digit:
                summary["skipped_no_vendor_number"] += 1
                continue
            ecode = engine_code(p.engine_model or "")
            ccode = self.code_for_category(p.category)
            seq = counters.get((ecode, ccode), 0) + 1
            counters[(ecode, ccode)] = seq
            new_sku = assemble_sku(ccode, digit, seq, engine_code=ecode)
            plan.append((p, src, p.sku, new_sku, ecode, ccode, seq))
            summary["by_vendor_digit"][digit] = summary["by_vendor_digit"].get(digit, 0) + 1
            if len(summary["samples"]) < 20:
                summary["samples"].append(
                    {"old": p.sku, "new": new_sku, "title": (p.title or "")[:48]}
                )
        summary["regenerated"] = len(plan)

        if apply and plan:
            for p, *_ in plan:                       # phase 1 — stage unique temps
                p.sku = f"__SKUTMP__{p.id}"
            self.db.flush()
            for p, src, old_sku, new_sku, ecode, ccode, seq in plan:   # phase 2 — finals
                p.engine_code = ecode
                p.category_code = ccode
                p.part_seq = seq
                p.sku = new_sku
                if not (src.vendor_sku or "").strip():   # keep old number searchable
                    src.vendor_sku = old_sku or ""
            self.db.commit()
        return summary
