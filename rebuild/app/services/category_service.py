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

from app.models.product import Brand, Manufacturer, Product, ProductCategory
from app.services.base import BaseService

# Level → human label for the 3-level tree (§18.3).
LEVEL_LABELS: dict[int, str] = {1: "Category", 2: "Subcategory", 3: "Product Family"}
MAX_LEVEL = 3


class CategoryService(BaseService):

    # ══ Category tree ══════════════════════════════════════════════════════════
    def category_tree(self) -> list[dict]:
        """Flat, display-ordered rows: parent immediately followed by its
        descendants; each sibling level sorted by (sort_order, name). Each row =
        {cat, depth, label, product_count}."""
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

        rows: list[dict] = []

        def _walk(parent_id: int | None, depth: int) -> None:
            for c in by_parent.get(parent_id, []):
                rows.append({
                    "cat": c,
                    "depth": depth,
                    "label": LEVEL_LABELS.get(c.level, f"L{c.level}"),
                    "product_count": counts.get(c.id, 0),
                })
                _walk(c.id, depth + 1)

        _walk(None, 0)
        return rows

    def categories_flat(self, include_inactive: bool = True) -> list[ProductCategory]:
        """All categories sorted by full_path — used for the parent <select>."""
        q = self.db.query(ProductCategory)
        if not include_inactive:
            q = q.filter(ProductCategory.is_active == True)  # noqa: E712
        return sorted(q.all(), key=lambda c: c.full_path.lower())

    def create_category(
        self, name: str, parent_id: int | None = None, sort_order: int = 0,
        default_markup_pct: float | None = None, import_keywords: str = "",
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
