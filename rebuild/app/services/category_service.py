"""
app/services/category_service.py
================================
§18 Category Maintenance — CRUD for the three owner-maintained classification
axes that the Inventory → Category Maintenance screen edits:

  • ProductCategory  — the Category → Subcategory → Product Family tree
                       (self-referential; level 1/2/3). Carries sort_order,
                       default_markup_pct, and import_keywords (§18.6 rules).
  • Brand            — the parts brand (PAI / Interstate-McBee / SAMPA / JAK'S).
  • Manufacturer     — Manufacturer / Engine Make (Cummins, CAT, …) — the SAME
                       concept as Product.engine_manufacturer (§18 A1).

These three are DISTINCT from each other and from Vendor (who we buy from).
See MASTER_PLAN.md §18.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import AuditAction, Permission
from app.models.product import Brand, Manufacturer, Product, ProductCategory
from app.services.base import BaseService

# Level → human label for the 3-level tree (§18.3).
LEVEL_LABELS: dict[int, str] = {1: "Category", 2: "Subcategory", 3: "Product Family"}
MAX_LEVEL = 3


def _clean_category_code(code) -> str:
    """SKU [CATEGORY] segment hygiene: alnum-only uppercase, max 6 chars —
    matches the charset of SkuService's derived fallback so a typed code can
    never inject spaces/punctuation into assembled JAKS SKUs."""
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())[:6]


class CategoryService(BaseService):

    # ══ Category tree ══════════════════════════════════════════════════════════
    def category_tree(self) -> list[dict]:
        """Flat, display-ordered rows: parent immediately followed by its
        descendants; each sibling level sorted by (sort_order, name). Each row =
        {cat, depth, label, product_count, total_count, reparent_targets,
        merge_targets} — total_count is descendant-inclusive (R3-4) and the two
        target lists feed the Move-to / Merge-into selects (server re-validates
        regardless)."""
        cats = self.db.query(ProductCategory).all()
        counts = dict(
            self.db.query(Product.category_id, func.count(Product.id))
            .group_by(Product.category_id)
            .all()
        )
        by_parent: dict[int | None, list[ProductCategory]] = {}
        for c in cats:
            by_parent.setdefault(c.parent_id, []).append(c)
        for lst in by_parent.values():
            lst.sort(key=lambda c: (c.sort_order or 0, (c.name or "").lower()))

        # Subtree aggregates per node: descendant ids, deepest level, and
        # descendant-inclusive product count.
        desc_ids: dict[int, set[int]] = {}
        deepest: dict[int, int] = {}
        totals: dict[int, int] = {}

        def _gather(c: ProductCategory) -> None:
            ids: set[int] = set()
            deep = c.level
            total = counts.get(c.id, 0)
            for ch in by_parent.get(c.id, []):
                _gather(ch)
                ids |= {ch.id} | desc_ids[ch.id]
                deep = max(deep, deepest[ch.id])
                total += totals[ch.id]
            desc_ids[c.id], deepest[c.id], totals[c.id] = ids, deep, total

        for top in by_parent.get(None, []):
            _gather(top)

        active_sorted = sorted(
            (c for c in cats if c.is_active), key=lambda c: c.full_path.lower()
        )

        rows: list[dict] = []

        def _walk(parent_id: int | None, depth: int) -> None:
            for c in by_parent.get(parent_id, []):
                ds = desc_ids.get(c.id, set())
                # Height of the subtree BELOW c (0 = leaf).
                extra = deepest.get(c.id, c.level) - c.level
                rows.append({
                    "cat": c,
                    "depth": depth,
                    "label": LEVEL_LABELS.get(c.level, f"L{c.level}"),
                    "product_count": counts.get(c.id, 0),
                    "total_count": totals.get(c.id, counts.get(c.id, 0)),
                    # R3-1: parents whose adoption of c keeps the whole moved
                    # subtree within the 3-level cap (excl. self/descendants/
                    # current parent).
                    "reparent_targets": [
                        p for p in active_sorted
                        if p.id != c.id and p.id not in ds
                        and p.id != c.parent_id
                        and p.level + 1 + extra <= MAX_LEVEL
                    ],
                    # R3-2: destinations that could adopt c's children without
                    # breaking the cap (excl. self/descendants).
                    "merge_targets": [
                        p for p in active_sorted
                        if p.id != c.id and p.id not in ds
                        and p.level + extra <= MAX_LEVEL
                    ],
                })
                _walk(c.id, depth + 1)

        _walk(None, 0)
        return rows

    def _subtree(self, root_id: int) -> list[ProductCategory]:
        """All strict descendants of ``root_id`` (children, grandchildren, …)."""
        by_parent: dict[int | None, list[ProductCategory]] = {}
        for c in self.db.query(ProductCategory).all():
            by_parent.setdefault(c.parent_id, []).append(c)
        out: list[ProductCategory] = []
        stack = list(by_parent.get(root_id, []))
        while stack:
            n = stack.pop()
            out.append(n)
            stack.extend(by_parent.get(n.id, []))
        return out

    def categories_flat(self, include_inactive: bool = True) -> list[ProductCategory]:
        """All categories sorted by full_path — used for the parent <select>."""
        q = self.db.query(ProductCategory)
        if not include_inactive:
            q = q.filter(ProductCategory.is_active == True)  # noqa: E712
        return sorted(q.all(), key=lambda c: c.full_path.lower())

    def create_category(
        self, name: str, parent_id: int | None = None, sort_order: int = 0,
        default_markup_pct: float | None = None, import_keywords: str = "",
        code: str = "",
    ) -> ProductCategory:
        name = (name or "").strip()
        if not name:
            raise ValueError("Category name is required.")
        level = 1
        if parent_id:
            parent = self.db.get(ProductCategory, parent_id)
            if parent is None:
                raise ValueError("Parent category not found.")
            if parent.level >= MAX_LEVEL:
                raise ValueError(
                    f"Cannot nest deeper than {LEVEL_LABELS[MAX_LEVEL]} (3 levels)."
                )
            level = parent.level + 1
        cat = ProductCategory(
            name=name[:200], parent_id=parent_id or None, level=level, is_active=True,
            sort_order=int(sort_order or 0), default_markup_pct=default_markup_pct,
            import_keywords=(import_keywords or "").strip(),
            # SKU [CATEGORY] segment (R1-7b). Blank → SkuService derives a fallback.
            code=_clean_category_code(code),
        )
        self.db.add(cat)
        self.db.commit()
        self.db.refresh(cat)
        return cat

    def update_category(self, cat_id: int, **fields) -> ProductCategory:
        cat = self.db.get(ProductCategory, cat_id)
        if cat is None:
            raise ValueError("Category not found.")
        if fields.get("name"):
            cat.name = str(fields["name"]).strip()[:200]
        if fields.get("sort_order") is not None:
            cat.sort_order = int(fields["sort_order"])
        if "default_markup_pct" in fields:
            cat.default_markup_pct = fields["default_markup_pct"]
        if fields.get("import_keywords") is not None:
            cat.import_keywords = str(fields["import_keywords"]).strip()
        if fields.get("code") is not None:
            # Blank clears the explicit code → SkuService falls back to derived.
            cat.code = _clean_category_code(fields["code"])
        if fields.get("is_active") is not None:
            cat.is_active = bool(fields["is_active"])
        self.db.commit()
        self.db.refresh(cat)
        return cat

    def set_category_active(self, cat_id: int, active: bool) -> ProductCategory:
        return self.update_category(cat_id, is_active=active)

    def delete_category(self, cat_id: int) -> str:
        """Hard-delete only if it has no children AND no products attached.
        Otherwise deactivate (never orphan products or sub-nodes). Returns the
        action taken: 'deleted' | 'deactivated'."""
        cat = self.db.get(ProductCategory, cat_id)
        if cat is None:
            return "deleted"
        has_children = (
            self.db.query(ProductCategory.id)
            .filter(ProductCategory.parent_id == cat_id).first() is not None
        )
        has_products = (
            self.db.query(Product.id)
            .filter(Product.category_id == cat_id).first() is not None
        )
        if has_children or has_products:
            cat.is_active = False
            self.db.commit()
            return "deactivated"
        self.db.delete(cat)
        self.db.commit()
        return "deleted"

    # ══ R3 — post-import cleanup tools (reparent / merge) ══════════════════════
    def reparent_category(self, cat_id: int, new_parent_id: int | None) -> ProductCategory:
        """Move a category (and its whole subtree) under a new parent, or to the
        top level when ``new_parent_id`` is falsy. Validates: target exists and
        is active, no self/cycle, and the recomputed level of the node AND every
        descendant stays within MAX_LEVEL."""
        cat = self.db.get(ProductCategory, cat_id)
        if cat is None:
            raise ValueError("Category not found.")
        new_parent_id = new_parent_id or None
        new_level = 1
        if new_parent_id:
            if new_parent_id == cat_id:
                raise ValueError("A category cannot be moved under itself.")
            parent = self.db.get(ProductCategory, new_parent_id)
            if parent is None:
                raise ValueError("Target parent not found.")
            if not parent.is_active:
                raise ValueError("Target parent is inactive.")
            new_level = parent.level + 1

        descendants = self._subtree(cat_id)
        if new_parent_id and new_parent_id in {d.id for d in descendants}:
            raise ValueError("Cannot move a category under one of its own descendants.")

        shift = new_level - cat.level
        deepest = max([cat.level] + [d.level for d in descendants])
        if deepest + shift > MAX_LEVEL:
            raise ValueError(
                f"Move would nest deeper than {LEVEL_LABELS[MAX_LEVEL]} (3 levels)"
                " — reparent its children first."
            )

        cat.parent_id = new_parent_id
        cat.level = new_level
        for d in descendants:
            d.level += shift
        self.db.commit()
        self.db.refresh(cat)
        return cat

    def merge_category(self, src_id: int, dest_id: int) -> dict:
        """Merge ``src`` into ``dest``: reassign ALL of src's products to dest,
        adopt src's children into dest (blocked with a clear error if that would
        exceed MAX_LEVEL — reparent the children first), union the two
        import_keywords lists onto dest, then soft-delete (deactivate) src.
        Returns {'products_moved', 'children_adopted', 'dest_name'}."""
        # Irreversible bulk reassignment across the live catalog — products carry
        # no back-pointer to their pre-merge category, so the audit row below is
        # the only record of what moved.
        self.assert_can(Permission.MERGE_CATALOG)
        if src_id == dest_id:
            raise ValueError("Cannot merge a category into itself.")
        src = self.db.get(ProductCategory, src_id)
        if src is None:
            raise ValueError("Source category not found.")
        dest = self.db.get(ProductCategory, dest_id)
        if dest is None:
            raise ValueError("Destination category not found.")
        if not dest.is_active:
            raise ValueError("Destination category is inactive.")

        descendants = self._subtree(src_id)
        if dest_id in {d.id for d in descendants}:
            raise ValueError(
                "Cannot merge a category into one of its own descendants"
                " — reparent its children first."
            )
        children = [d for d in descendants if d.parent_id == src_id]
        shift = dest.level - src.level
        if descendants and max(d.level for d in descendants) + shift > MAX_LEVEL:
            raise ValueError(
                f"Merging would nest its children deeper than {LEVEL_LABELS[MAX_LEVEL]}"
                " (3 levels) — reparent the children first."
            )

        # Products → dest.
        products_moved = (
            self.db.query(Product)
            .filter(Product.category_id == src_id)
            .update({"category_id": dest_id}, synchronize_session=False)
        )

        # import_keywords → union (dest's first), comma-joined, case-insensitive dedupe.
        def _kw_list(raw: str | None) -> list[str]:
            return [t.strip() for t in (raw or "").replace("\n", ",").split(",") if t.strip()]

        merged: list[str] = []
        seen: set[str] = set()
        for kw in _kw_list(dest.import_keywords) + _kw_list(src.import_keywords):
            if kw.lower() not in seen:
                seen.add(kw.lower())
                merged.append(kw)
        dest.import_keywords = ", ".join(merged)

        # Children (and their subtrees) → adopted by dest, levels recomputed.
        for child in children:
            child.parent_id = dest_id
        for d in descendants:
            d.level += shift

        src.is_active = False
        self.audit(
            entity_type="category",
            entity_id=src_id,
            action=AuditAction.EDITED,
            old_value={"src_id": src_id, "src_name": src.name},
            new_value={
                "dest_id": dest_id,
                "dest_name": dest.name,
                "products_moved": int(products_moved or 0),
                "children_adopted": len(children),
            },
            notes=f"Merged category '{src.name}' into '{dest.name}'",
        )
        self.db.commit()
        return {
            "products_moved": int(products_moved or 0),
            "children_adopted": len(children),
            "dest_name": dest.name,
        }

    # ══ Brands ═════════════════════════════════════════════════════════════════
    def brands(self, include_inactive: bool = True) -> list[Brand]:
        q = self.db.query(Brand)
        if not include_inactive:
            q = q.filter(Brand.is_active == True)  # noqa: E712
        return q.order_by(Brand.sort_order, Brand.name).all()

    def create_brand(self, name: str, is_house_brand: bool = False, sort_order: int = 0) -> Brand:
        name = (name or "").strip()
        if not name:
            raise ValueError("Brand name is required.")
        if self.db.query(Brand).filter(func.lower(Brand.name) == name.lower()).first():
            raise ValueError(f"Brand '{name}' already exists.")
        b = Brand(name=name[:200], is_house_brand=bool(is_house_brand),
                  sort_order=int(sort_order or 0), is_active=True)
        self.db.add(b)
        self.db.commit()
        self.db.refresh(b)
        return b

    def update_brand(self, brand_id: int, **fields) -> Brand:
        b = self.db.get(Brand, brand_id)
        if b is None:
            raise ValueError("Brand not found.")
        if fields.get("name"):
            nm = str(fields["name"]).strip()
            clash = (
                self.db.query(Brand)
                .filter(func.lower(Brand.name) == nm.lower(), Brand.id != brand_id)
                .first()
            )
            if clash:
                raise ValueError(f"Brand '{nm}' already exists.")
            old_name = b.name
            # R1-6: Product.brand is free-text (no FK) — cascade the rename so
            # tagged parts stay attached to the brand (mirror of manufacturer).
            # §21 — fire on ANY rename (incl. a case-only fix), and match
            # case-INSENSITIVELY so bulk-imported casing variants ('PAI'/'pai'/
            # 'Pai') are all re-tagged instead of orphaned from the brand filter.
            # The cascade bulk-rewrites free-text Product.brand with no back-
            # pointer — same blast radius as merge_brand, so same gate + audit.
            if old_name and old_name != nm[:200]:
                self.assert_can(Permission.MERGE_CATALOG)
                b.name = nm[:200]
                retagged = self.db.query(Product).filter(
                    func.lower(Product.brand) == old_name.lower()
                ).update({"brand": b.name}, synchronize_session=False)
                self.audit(
                    entity_type="brand", entity_id=b.id, action=AuditAction.EDITED,
                    old_value=old_name, new_value=b.name,
                    notes=f"Rename cascade re-tagged {retagged} product(s)",
                )
            else:
                b.name = nm[:200]
        if fields.get("sort_order") is not None:
            b.sort_order = int(fields["sort_order"])
        if fields.get("is_house_brand") is not None:
            b.is_house_brand = bool(fields["is_house_brand"])
        if fields.get("is_active") is not None:
            b.is_active = bool(fields["is_active"])
        self.db.commit()
        self.db.refresh(b)
        return b

    def delete_brand(self, brand_id: int) -> None:
        b = self.db.get(Brand, brand_id)
        if b is None:
            return
        # Soft-deactivate if any product carries this brand; else hard-delete.
        in_use = (
            self.db.query(Product.id)
            .filter(func.lower(Product.brand) == (b.name or "").lower()).first() is not None
        )
        if in_use:
            b.is_active = False
        else:
            self.db.delete(b)
        self.db.commit()

    def merge_brand(self, src_id: int, dest_id: int) -> dict:
        """§23.3 Phase 2 — merge a duplicate brand into another (case-only
        typos, "PAI"/"Pai Industries", etc.). Mirrors merge_category: reassign
        every Product carrying src's name (case-insensitive — the same match
        rule update_brand's own rename cascade uses) to dest's name, then
        soft-deactivate src. ALWAYS soft-deactivates (never hard-deletes,
        unlike delete_brand) — a merged-away brand should stay resolvable in
        history/audit, not disappear. Returns {'products_moved', 'dest_name'}."""
        # Irreversible: Product.brand is free-text, so once overwritten there is
        # no back-pointer — the audit row below is the only record of what moved.
        self.assert_can(Permission.MERGE_CATALOG)
        if src_id == dest_id:
            raise ValueError("Cannot merge a brand into itself.")
        src = self.db.get(Brand, src_id)
        if src is None:
            raise ValueError("Source brand not found.")
        dest = self.db.get(Brand, dest_id)
        if dest is None:
            raise ValueError("Destination brand not found.")
        if not dest.is_active:
            raise ValueError("Destination brand is inactive.")

        products_moved = (
            self.db.query(Product)
            .filter(func.lower(Product.brand) == (src.name or "").lower())
            .update({"brand": dest.name}, synchronize_session=False)
        )
        src.is_active = False
        self.audit(
            entity_type="brand",
            entity_id=src_id,
            action=AuditAction.EDITED,
            old_value={"src_id": src_id, "src_name": src.name},
            new_value={
                "dest_id": dest_id,
                "dest_name": dest.name,
                "products_moved": int(products_moved or 0),
            },
            notes=f"Merged brand '{src.name}' into '{dest.name}'",
        )
        self.db.commit()
        return {"products_moved": int(products_moved or 0), "dest_name": dest.name}

    # ══ Manufacturers (= Engine Make, §18 A1) ══════════════════════════════════
    def manufacturers(self, include_inactive: bool = True) -> list[Manufacturer]:
        q = self.db.query(Manufacturer)
        if not include_inactive:
            q = q.filter(Manufacturer.is_active == True)  # noqa: E712
        return q.order_by(Manufacturer.sort_order, Manufacturer.name).all()

    def create_manufacturer(self, name: str, sort_order: int = 0) -> Manufacturer:
        name = (name or "").strip()
        if not name:
            raise ValueError("Manufacturer name is required.")
        if self.db.query(Manufacturer).filter(func.lower(Manufacturer.name) == name.lower()).first():
            raise ValueError(f"Manufacturer '{name}' already exists.")
        m = Manufacturer(name=name[:200], sort_order=int(sort_order or 0), is_active=True)
        self.db.add(m)
        self.db.commit()
        self.db.refresh(m)
        return m

    def update_manufacturer(self, man_id: int, **fields) -> Manufacturer:
        m = self.db.get(Manufacturer, man_id)
        if m is None:
            raise ValueError("Manufacturer not found.")
        if fields.get("name"):
            nm = str(fields["name"]).strip()
            clash = (
                self.db.query(Manufacturer)
                .filter(func.lower(Manufacturer.name) == nm.lower(), Manufacturer.id != man_id)
                .first()
            )
            if clash:
                raise ValueError(f"Manufacturer '{nm}' already exists.")
            old_name = m.name
            # R1-6: Product.engine_manufacturer is free-text (no FK), so a rename
            # here must cascade or every tagged part drops out of the engine-make
            # filter. §21 — fire on ANY rename (incl. a case-only fix) and match
            # case-INSENSITIVELY so casing-variant imported rows aren't orphaned.
            # Same blast radius as merge_manufacturer → same gate + audit.
            if old_name and old_name != nm[:200]:
                self.assert_can(Permission.MERGE_CATALOG)
                m.name = nm[:200]
                retagged = self.db.query(Product).filter(
                    func.lower(Product.engine_manufacturer) == old_name.lower()
                ).update({"engine_manufacturer": m.name}, synchronize_session=False)
                self.audit(
                    entity_type="manufacturer", entity_id=m.id, action=AuditAction.EDITED,
                    old_value=old_name, new_value=m.name,
                    notes=f"Rename cascade re-tagged {retagged} product(s)",
                )
            else:
                m.name = nm[:200]
        if fields.get("sort_order") is not None:
            m.sort_order = int(fields["sort_order"])
        if fields.get("is_active") is not None:
            m.is_active = bool(fields["is_active"])
        self.db.commit()
        self.db.refresh(m)
        return m

    def delete_manufacturer(self, man_id: int) -> None:
        m = self.db.get(Manufacturer, man_id)
        if m is None:
            return
        in_use = (
            self.db.query(Product.id)
            .filter(func.lower(Product.engine_manufacturer) == (m.name or "").lower()).first() is not None
        )
        if in_use:
            m.is_active = False
        else:
            self.db.delete(m)
        self.db.commit()

    def merge_manufacturer(self, src_id: int, dest_id: int) -> dict:
        """§23.3 Phase 2 — merge a duplicate manufacturer/engine-make into
        another. Reassigns BOTH Product.manufacturer AND
        Product.engine_manufacturer — the model docstring calls these "the
        SAME concept" and both now read this one managed list (see
        engine_make_names + routers/products.py's product-form dropdown) — a
        merge that only fixed one would leave the other silently pointing at
        a deactivated name. update_manufacturer's own rename cascade only
        touches engine_manufacturer; merge is the broader operation.
        ALWAYS soft-deactivates src (never hard-deletes) so it stays
        resolvable in history/audit. Returns {'products_moved', 'dest_name'}
        where products_moved counts distinct products touched on EITHER field."""
        # Irreversible: both manufacturer fields are free-text, so once
        # overwritten there is no back-pointer — the audit row below is the
        # only record of what moved.
        self.assert_can(Permission.MERGE_CATALOG)
        if src_id == dest_id:
            raise ValueError("Cannot merge a manufacturer into itself.")
        src = self.db.get(Manufacturer, src_id)
        if src is None:
            raise ValueError("Source manufacturer not found.")
        dest = self.db.get(Manufacturer, dest_id)
        if dest is None:
            raise ValueError("Destination manufacturer not found.")
        if not dest.is_active:
            raise ValueError("Destination manufacturer is inactive.")

        src_name_lower = (src.name or "").lower()
        touched_ids = {
            pid for (pid,) in self.db.query(Product.id).filter(
                func.lower(Product.manufacturer) == src_name_lower
            ).all()
        }
        touched_ids |= {
            pid for (pid,) in self.db.query(Product.id).filter(
                func.lower(Product.engine_manufacturer) == src_name_lower
            ).all()
        }
        self.db.query(Product).filter(
            func.lower(Product.manufacturer) == src_name_lower
        ).update({"manufacturer": dest.name}, synchronize_session=False)
        self.db.query(Product).filter(
            func.lower(Product.engine_manufacturer) == src_name_lower
        ).update({"engine_manufacturer": dest.name}, synchronize_session=False)

        src.is_active = False
        self.audit(
            entity_type="manufacturer",
            entity_id=src_id,
            action=AuditAction.EDITED,
            old_value={"src_id": src_id, "src_name": src.name},
            new_value={
                "dest_id": dest_id,
                "dest_name": dest.name,
                "products_moved": len(touched_ids),
            },
            notes=f"Merged manufacturer '{src.name}' into '{dest.name}'",
        )
        self.db.commit()
        return {"products_moved": len(touched_ids), "dest_name": dest.name}


def engine_make_names(db: Session, *, include_other: bool = True) -> list[str]:
    """§23.3 Phase 1 #5 — the engine_picker macro's Make dropdown, sourced from
    the owner-maintained Manufacturer table instead of the hardcoded
    constants.ENGINE_MAKES list. Product.manufacturer filters already read this
    table (see routers/products.py); the picker used a SEPARATE static list
    that could silently drift the moment an owner edited manufacturers via
    Category Maintenance. seed_manufacturers() deliberately excludes "Other"
    (a UI free-text escape hatch, not a real manufacturer row) — appended here
    so the picker's "always reachable custom value" invariant still holds.
    constants.ENGINE_MODELS_BY_MAKE stays a constant (models aren't DB-backed);
    a make with no models entry gracefully falls back to ['Other'] in the
    macro's own JS (engine_picker.html modelOptions getter)."""
    names = [m.name for m in CategoryService(db).manufacturers(include_inactive=False)]
    if include_other and "Other" not in names:
        names.append("Other")
    return names


def manufacturer_names(db: Session) -> list[str]:
    """§23.3 Phase 2 — the product-form Manufacturer dropdown's option list.
    Product.manufacturer is "the SAME concept" as Product.engine_manufacturer
    per the Manufacturer model docstring, so BOTH fields now read this one
    owner-maintained table — previously this dropdown read a hardcoded
    8-entry constant (products.py MANUFACTURERS) that silently drifted from
    whatever the owner edited in Category Maintenance. Unlike
    engine_make_names, no "Other" append — the product form's legacy-
    preserving fallback (an unlisted stored value still renders selected)
    is the existing escape hatch, matching how it already worked before this
    fix. NOTE: routers/products.py's own MANUFACTURERS constant is untouched
    — it's a separate, load-bearing import-canonicalization vocabulary used
    by product_import_service.py / import_review_service.py /
    shopify_service.py, not a UI list."""
    return [m.name for m in CategoryService(db).manufacturers(include_inactive=False)]


def brand_names(db: Session) -> list[str]:
    """§23.3 Phase 2 — the product-form Brand dropdown's option list, sourced
    from the owner-maintained Brand table (Category Maintenance) instead of a
    free-text input with no managed-list backing at all."""
    return [b.name for b in CategoryService(db).brands(include_inactive=False)]
