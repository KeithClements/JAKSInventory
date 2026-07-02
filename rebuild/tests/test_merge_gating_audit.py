"""
tests/test_merge_gating_audit.py
================================
RBAC + audit gate on the three catalog merge operations (category / brand /
manufacturer). Each merge is an irreversible bulk reassignment across the live
catalog — products carry no back-pointer to their pre-merge value — so:

  • merge_category / merge_brand / merge_manufacturer require
    Permission.MERGE_CATALOG (ADMIN-only in the role matrix);
  • every successful merge writes an AuditLog row recording src/dest and the
    count of products moved;
  • the /categories merge routes convert PermissionDeniedError into the
    router's standard redirect-with-?error= flash (never a 500).

RBAC tests create REAL User rows (see BaseService.assert_can: under
JAKS_SKIP_AUTH only UNKNOWN actor ids bypass — a seeded user's role is always
enforced), mirroring tests/test_s22_bill_approval.py.
"""
from __future__ import annotations

import itertools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.auth as auth
import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401 — registers every model on Base

from app.auth import SESSION_COOKIE, make_session_token
from app.constants import AuditAction, UserRole
from app.main import app as _fastapi_app
from app.models.audit import AuditLog
from app.models.product import Brand, Manufacturer, Product, ProductCategory
from app.models.user import User
from app.services.base import PermissionDeniedError
from app.services.category_service import CategoryService

_counter = itertools.count(1)


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    activate(fresh_engine())
    # Session-cookie signing secret must come from THIS module's engine, not a
    # previous module's cached one (route-level tests forge cookies).
    auth.reset_secret_cache()
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _user(db, role: str) -> User:
    n = next(_counter)
    u = User(name=f"{role}-{n}", username=f"{role}_{n}",
             password_hash="x", role=role)
    db.add(u); db.commit(); db.refresh(u)
    return u


def _seed_categories(db) -> tuple[ProductCategory, ProductCategory, Product]:
    """Two top-level categories + one product filed under src."""
    n = next(_counter)
    src = ProductCategory(name=f"Src Cat {n}", level=1, is_active=True)
    dest = ProductCategory(name=f"Dest Cat {n}", level=1, is_active=True)
    db.add_all([src, dest]); db.flush()
    prod = Product(sku=f"SKU-MRG-{n:06d}", title=f"Merge Part {n}",
                   qty_on_hand=0, qty_committed=0, cost=10.0,
                   category_id=src.id)
    db.add(prod); db.commit()
    db.refresh(src); db.refresh(dest); db.refresh(prod)
    return src, dest, prod


def _seed_brands(db) -> tuple[Brand, Brand, Product]:
    """Two brands + one product tagged (free-text) with src's name."""
    n = next(_counter)
    src = Brand(name=f"SrcBrand{n}", is_active=True)
    dest = Brand(name=f"DestBrand{n}", is_active=True)
    db.add_all([src, dest]); db.flush()
    prod = Product(sku=f"SKU-BRD-{n:06d}", title=f"Brand Part {n}",
                   qty_on_hand=0, qty_committed=0, cost=10.0,
                   brand=src.name)
    db.add(prod); db.commit()
    db.refresh(src); db.refresh(dest); db.refresh(prod)
    return src, dest, prod


def _seed_manufacturers(db) -> tuple[Manufacturer, Manufacturer, Product]:
    """Two manufacturers + one product tagged with src on BOTH free-text fields."""
    n = next(_counter)
    src = Manufacturer(name=f"SrcMfg{n}", is_active=True)
    dest = Manufacturer(name=f"DestMfg{n}", is_active=True)
    db.add_all([src, dest]); db.flush()
    prod = Product(sku=f"SKU-MFG-{n:06d}", title=f"Mfg Part {n}",
                   qty_on_hand=0, qty_committed=0, cost=10.0,
                   manufacturer=src.name, engine_manufacturer=src.name)
    db.add(prod); db.commit()
    db.refresh(src); db.refresh(dest); db.refresh(prod)
    return src, dest, prod


def _audit_rows(db, entity_type: str) -> list[AuditLog]:
    return db.query(AuditLog).filter(AuditLog.entity_type == entity_type).all()


# ══════════════════════════════════════════════════════════════════════════════
# 1 — SALES (and READ_ONLY) are denied; nothing is reassigned
# ══════════════════════════════════════════════════════════════════════════════

class TestMergeDenied:

    def test_sales_cannot_merge_category(self, db):
        src, dest, prod = _seed_categories(db)
        sales = _user(db, UserRole.SALES)

        with pytest.raises(PermissionDeniedError):
            CategoryService(db, current_user_id=sales.id).merge_category(src.id, dest.id)
        db.rollback()
        db.refresh(prod); db.refresh(src)
        assert prod.category_id == src.id, "denied merge must not move products"
        assert src.is_active is True, "denied merge must not deactivate the source"
        assert _audit_rows(db, "category") == []

    def test_sales_cannot_merge_brand(self, db):
        src, dest, prod = _seed_brands(db)
        sales = _user(db, UserRole.SALES)

        with pytest.raises(PermissionDeniedError):
            CategoryService(db, current_user_id=sales.id).merge_brand(src.id, dest.id)
        db.rollback()
        db.refresh(prod); db.refresh(src)
        assert prod.brand == src.name
        assert src.is_active is True
        assert _audit_rows(db, "brand") == []

    def test_sales_cannot_merge_manufacturer(self, db):
        src, dest, prod = _seed_manufacturers(db)
        sales = _user(db, UserRole.SALES)

        with pytest.raises(PermissionDeniedError):
            CategoryService(db, current_user_id=sales.id).merge_manufacturer(src.id, dest.id)
        db.rollback()
        db.refresh(prod); db.refresh(src)
        assert prod.manufacturer == src.name
        assert prod.engine_manufacturer == src.name
        assert src.is_active is True
        assert _audit_rows(db, "manufacturer") == []

    def test_read_only_cannot_merge_category(self, db):
        src, dest, prod = _seed_categories(db)
        ro = _user(db, UserRole.READ_ONLY)

        with pytest.raises(PermissionDeniedError):
            CategoryService(db, current_user_id=ro.id).merge_category(src.id, dest.id)
        db.rollback()
        db.refresh(prod)
        assert prod.category_id == src.id


# ══════════════════════════════════════════════════════════════════════════════
# 2 — ADMIN merge succeeds AND writes an audit row with the moved count
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminMergeAudited:

    def test_admin_merge_category_moves_and_audits(self, db):
        src, dest, prod = _seed_categories(db)
        admin = _user(db, UserRole.ADMIN)

        result = CategoryService(db, current_user_id=admin.id).merge_category(src.id, dest.id)
        assert result["products_moved"] == 1

        db.refresh(prod); db.refresh(src)
        assert prod.category_id == dest.id
        assert src.is_active is False

        rows = _audit_rows(db, "category")
        assert len(rows) == 1
        row = rows[0]
        assert row.entity_id == src.id
        assert row.action == AuditAction.EDITED
        assert row.user_id == admin.id
        old = json.loads(row.old_value)
        new = json.loads(row.new_value)
        assert old["src_id"] == src.id and old["src_name"] == src.name
        assert new["dest_id"] == dest.id and new["dest_name"] == dest.name
        assert new["products_moved"] == 1
        assert new["children_adopted"] == 0

    def test_admin_merge_brand_moves_and_audits(self, db):
        src, dest, prod = _seed_brands(db)
        admin = _user(db, UserRole.ADMIN)

        result = CategoryService(db, current_user_id=admin.id).merge_brand(src.id, dest.id)
        assert result["products_moved"] == 1

        db.refresh(prod); db.refresh(src)
        assert prod.brand == dest.name
        assert src.is_active is False

        rows = _audit_rows(db, "brand")
        assert len(rows) == 1
        row = rows[0]
        assert row.entity_id == src.id
        assert row.user_id == admin.id
        new = json.loads(row.new_value)
        assert new["dest_id"] == dest.id
        assert new["products_moved"] == 1

    def test_admin_merge_manufacturer_moves_and_audits(self, db):
        src, dest, prod = _seed_manufacturers(db)
        admin = _user(db, UserRole.ADMIN)

        result = CategoryService(db, current_user_id=admin.id).merge_manufacturer(src.id, dest.id)
        assert result["products_moved"] == 1  # one product touched on both fields

        db.refresh(prod); db.refresh(src)
        assert prod.manufacturer == dest.name
        assert prod.engine_manufacturer == dest.name
        assert src.is_active is False

        rows = _audit_rows(db, "manufacturer")
        assert len(rows) == 1
        row = rows[0]
        assert row.entity_id == src.id
        assert row.user_id == admin.id
        new = json.loads(row.new_value)
        assert new["dest_id"] == dest.id
        assert new["products_moved"] == 1

    def test_system_actor_still_allowed(self, db):
        """current_user_id=None is the system actor (background jobs) — passes
        the gate per BaseService.assert_can Phase-A semantics, and still audits."""
        src, dest, prod = _seed_categories(db)
        result = CategoryService(db, current_user_id=None).merge_category(src.id, dest.id)
        assert result["products_moved"] == 1
        rows = _audit_rows(db, "category")
        assert len(rows) == 1
        assert rows[0].user_id is None


# ══════════════════════════════════════════════════════════════════════════════
# 2b — The RENAME cascade in update_brand/update_manufacturer has the same
#      blast radius as a merge (bulk-rewrites free-text product tags with no
#      back-pointer) → same Permission.MERGE_CATALOG gate + audit row.
#      Non-rename edits (sort_order / is_active) stay open to any role.
# ══════════════════════════════════════════════════════════════════════════════

class TestRenameCascadeGated:

    # ── brand ──────────────────────────────────────────────────────────────────

    def test_sales_cannot_rename_brand(self, db):
        src, dest, prod = _seed_brands(db)
        old_name = src.name
        sales = _user(db, UserRole.SALES)

        with pytest.raises(PermissionDeniedError):
            CategoryService(db, current_user_id=sales.id).update_brand(
                src.id, name=f"{old_name} Renamed")
        db.rollback()
        db.refresh(prod); db.refresh(src)
        assert src.name == old_name, "denied rename must not change the brand"
        assert prod.brand == old_name, "denied rename must not re-tag products"
        assert _audit_rows(db, "brand") == []

    def test_admin_rename_brand_retags_and_audits(self, db):
        src, dest, prod = _seed_brands(db)
        old_name = src.name
        new_name = f"{old_name} Renamed"
        admin = _user(db, UserRole.ADMIN)

        b = CategoryService(db, current_user_id=admin.id).update_brand(
            src.id, name=new_name)
        assert b.name == new_name

        db.refresh(prod)
        assert prod.brand == new_name, "rename cascade must re-tag products"

        rows = _audit_rows(db, "brand")
        assert len(rows) == 1
        row = rows[0]
        assert row.entity_id == src.id
        assert row.action == AuditAction.EDITED
        assert row.user_id == admin.id
        assert row.old_value == old_name
        assert row.new_value == new_name
        assert "re-tagged 1 product(s)" in (row.notes or "")

    def test_sales_can_still_edit_brand_without_rename(self, db):
        """The gate is rename-only: sort_order / is_active edits stay open."""
        src, dest, prod = _seed_brands(db)
        sales = _user(db, UserRole.SALES)

        b = CategoryService(db, current_user_id=sales.id).update_brand(
            src.id, sort_order=7, is_active=True)
        assert b.sort_order == 7
        assert b.is_active is True

        db.refresh(prod)
        assert prod.brand == src.name          # nothing re-tagged
        assert _audit_rows(db, "brand") == []  # no cascade → no audit row

    # ── manufacturer ───────────────────────────────────────────────────────────

    def test_sales_cannot_rename_manufacturer(self, db):
        src, dest, prod = _seed_manufacturers(db)
        old_name = src.name
        sales = _user(db, UserRole.SALES)

        with pytest.raises(PermissionDeniedError):
            CategoryService(db, current_user_id=sales.id).update_manufacturer(
                src.id, name=f"{old_name} Renamed")
        db.rollback()
        db.refresh(prod); db.refresh(src)
        assert src.name == old_name
        assert prod.engine_manufacturer == old_name
        assert prod.manufacturer == old_name
        assert _audit_rows(db, "manufacturer") == []

    def test_admin_rename_manufacturer_retags_and_audits(self, db):
        src, dest, prod = _seed_manufacturers(db)
        old_name = src.name
        new_name = f"{old_name} Renamed"
        admin = _user(db, UserRole.ADMIN)

        m = CategoryService(db, current_user_id=admin.id).update_manufacturer(
            src.id, name=new_name)
        assert m.name == new_name

        db.refresh(prod)
        # The rename cascade is engine_manufacturer-only (merge_manufacturer is
        # the broader operation that also rewrites Product.manufacturer).
        assert prod.engine_manufacturer == new_name
        assert prod.manufacturer == old_name

        rows = _audit_rows(db, "manufacturer")
        assert len(rows) == 1
        row = rows[0]
        assert row.entity_id == src.id
        assert row.action == AuditAction.EDITED
        assert row.user_id == admin.id
        assert row.old_value == old_name
        assert row.new_value == new_name
        assert "re-tagged 1 product(s)" in (row.notes or "")

    def test_sales_can_still_edit_manufacturer_without_rename(self, db):
        src, dest, prod = _seed_manufacturers(db)
        sales = _user(db, UserRole.SALES)

        m = CategoryService(db, current_user_id=sales.id).update_manufacturer(
            src.id, sort_order=3, is_active=True)
        assert m.sort_order == 3
        assert m.is_active is True

        db.refresh(prod)
        assert prod.engine_manufacturer == src.name
        assert _audit_rows(db, "manufacturer") == []


# ══════════════════════════════════════════════════════════════════════════════
# 3 — Route level: a SALES session gets the clean ?error= redirect, not a 500
# ══════════════════════════════════════════════════════════════════════════════

def _client_as(user: User) -> TestClient:
    """TestClient carrying a forged (but validly signed) session cookie for
    ``user`` — get_current_user_id resolves it exactly like a real login."""
    client = TestClient(_fastapi_app, raise_server_exceptions=False)
    client.cookies.set(SESSION_COOKIE, make_session_token(user.id))
    return client


class TestMergeRoutesDenyCleanly:

    def test_sales_category_merge_route_redirects_with_error(self, db):
        src, dest, prod = _seed_categories(db)
        sales = _user(db, UserRole.SALES)

        r = _client_as(sales).post(
            f"/categories/category/{src.id}/merge-into/{dest.id}",
            follow_redirects=False,
        )
        assert r.status_code == 303, f"expected clean redirect, got {r.status_code}"
        assert "error=" in r.headers["location"]

        db.expire_all()
        assert db.get(Product, prod.id).category_id == src.id
        assert db.get(ProductCategory, src.id).is_active is True

    def test_sales_brand_merge_route_redirects_with_error(self, db):
        src, dest, prod = _seed_brands(db)
        sales = _user(db, UserRole.SALES)

        r = _client_as(sales).post(
            f"/categories/brand/{src.id}/merge-into/{dest.id}",
            follow_redirects=False,
        )
        assert r.status_code == 303, f"expected clean redirect, got {r.status_code}"
        assert "error=" in r.headers["location"]

        db.expire_all()
        assert db.get(Product, prod.id).brand == src.name

    def test_sales_manufacturer_merge_route_redirects_with_error(self, db):
        src, dest, prod = _seed_manufacturers(db)
        sales = _user(db, UserRole.SALES)

        r = _client_as(sales).post(
            f"/categories/manufacturer/{src.id}/merge-into/{dest.id}",
            follow_redirects=False,
        )
        assert r.status_code == 303, f"expected clean redirect, got {r.status_code}"
        assert "error=" in r.headers["location"]

        db.expire_all()
        assert db.get(Product, prod.id).manufacturer == src.name

    def test_admin_category_merge_route_succeeds(self, db):
        src, dest, prod = _seed_categories(db)
        admin = _user(db, UserRole.ADMIN)

        r = _client_as(admin).post(
            f"/categories/category/{src.id}/merge-into/{dest.id}",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "ok=" in r.headers["location"]

        db.expire_all()
        assert db.get(Product, prod.id).category_id == dest.id
