"""
app/services/classification_service.py
======================================
§18.6 — rule-driven product classification with a confidence gate.

Given a product's signals (Title, Tags, Body text, OEM refs, Applications) it
SUGGESTS:
  • engine_manufacturer / engine_model  — from the parsed Applications, mapped to
    canonical Manufacturer / Engine-Make names (§18 A1).
  • category_id                         — the DEEPEST category whose import_keywords
    (set on the Category Maintenance screen) match the text.

Confidence gate (§18.6): if it cannot confidently place the product into a real
Subcategory / Product Family it returns needs_review=True and leaves the deeper
category unset — the importer keeps the broad Shopify-Type category and the row
lands in the Import Review queue rather than being forced into a bad bucket.

Shopify TYPE is mapped to the top-level Category by the importer, NOT here — this
service only refines below that and never fabricates a category.
"""
from __future__ import annotations

from collections import Counter

from app.models.product import ProductCategory
from app.services.base import BaseService

# Raw application / engine-make token (substring) → canonical Engine-Make name.
# Keys are matched as case-insensitive substrings of the application's make token,
# so "CUMMINS", "Cummins ISX", "cummins" all resolve to "Cummins".
_MAKE_NORMALIZE: dict[str, str] = {
    "cummins": "Cummins",
    "caterpillar": "CAT / Caterpillar",
    "cat": "CAT / Caterpillar",
    "detroit": "Detroit",
    "mack": "Mack",
    "volvo": "Volvo",
    "international": "International / Navistar",
    "navistar": "International / Navistar",
    "paccar": "Paccar",
    "mercedes": "Mercedes",
    "mbe": "Mercedes",
}
_MIN_KEYWORD_LEN = 3


def normalize_make(raw: str) -> str:
    """Map a raw application make token to a canonical Engine-Make name, or ''."""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    # Longest keys first so 'caterpillar' wins over 'cat'.
    for key in sorted(_MAKE_NORMALIZE, key=len, reverse=True):
        if key in s:
            return _MAKE_NORMALIZE[key]
    return ""


class ClassificationService(BaseService):

    def __init__(self, db, current_user_id=None) -> None:
        super().__init__(db, current_user_id)
        self._rules_cache: list[tuple[int, str, int, list[str]]] | None = None

    def _category_rules(self) -> list[tuple[int, str, int, list[str]]]:
        """(category_id, name, level, keywords) for every ACTIVE category that has
        import_keywords, sorted DEEPEST level first so a Product-Family match beats a
        Category match. Built once per service instance. Stores PLAIN VALUES, not ORM
        objects, so the cache survives the periodic commits of a large import without
        a DetachedInstanceError when a keyword later hits."""
        if self._rules_cache is None:
            rules: list[tuple[int, str, int, list[str]]] = []
            cats = (
                self.db.query(ProductCategory)
                .filter(ProductCategory.is_active == True)  # noqa: E712
                .all()
            )
            for c in cats:
                raw = (c.import_keywords or "").replace("\n", ",")
                kws = [t.strip().lower() for t in raw.split(",")]
                kws = [k for k in kws if len(k) >= _MIN_KEYWORD_LEN]
                if kws:
                    rules.append((c.id, c.name, c.level, kws))
            rules.sort(key=lambda r: r[2], reverse=True)
            self._rules_cache = rules
        return self._rules_cache

    def classify(
        self, *, title: str = "", tags: str = "", extra_text: str = "",
        app_makes: list[str] | None = None, app_models: list[str] | None = None,
    ) -> dict:
        """Return a suggestion dict:
        {engine_manufacturer, engine_model, category_id, needs_review, reasons}."""
        app_makes = app_makes or []
        app_models = app_models or []
        reasons: list[str] = []

        # ── Engine make / model from applications (§18 A1) ──────────────────────
        canon = [m for m in (normalize_make(x) for x in app_makes) if m]
        engine_manufacturer = ""
        engine_model = ""
        if canon:
            distinct = set(canon)
            if len(distinct) == 1:
                engine_manufacturer = Counter(canon).most_common(1)[0][0]
                reasons.append(f"engine make ← applications ({engine_manufacturer})")
                models = [m.strip() for m in app_models if (m or "").strip()]
                if models and len({m.lower() for m in models}) == 1:
                    engine_model = models[0]
            else:
                reasons.append(f"multiple engine makes ({len(distinct)}) — left blank (multi-fit)")

        # ── Category refinement via keyword rules (deepest match wins) ──────────
        haystack = " ".join([title or "", tags or "", extra_text or ""]).lower()
        matched: tuple[int, str, int] | None = None
        for cid, cname, clevel, kws in self._category_rules():
            hit = next((k for k in kws if k in haystack), None)
            if hit:
                matched = (cid, cname, clevel)
                reasons.append(f"category '{cname}' ← keyword '{hit}'")
                break  # deepest-first → first hit is the most specific

        category_id = matched[0] if matched else None
        # Confident only when placed in a real Subcategory / Product Family (level>=2).
        needs_review = (matched is None) or (matched[2] < 2)
        if needs_review:
            reasons.append("needs review — no confident subcategory/family match")

        return {
            "engine_manufacturer": engine_manufacturer,
            "engine_model": engine_model,
            "category_id": category_id,
            "needs_review": needs_review,
            "reasons": reasons,
        }
