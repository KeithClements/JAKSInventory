"""
Phase 4 QBO chart-of-accounts mapping verification
==================================================
  - QBO_ACCOUNT_FIELDS covers the four owner accounts + income.
  - account_mappings() reads the settings (blank by default).
  - list_accounts() fails soft (no exception) when QBO isn't connected.
  - GET /settings/qbo-accounts renders without a live connection.
  - POST /settings/qbo-accounts (admin) persists the mappings.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import fresh_engine, activate
from app.main import app
from app.models.user import User
from app.constants import UserRole
from app.services.qbo_service import QBOSyncService, QBO_ACCOUNT_FIELDS


@pytest.fixture()
def Session():
    return activate(fresh_engine())


@pytest.fixture()
def client():
    return TestClient(app)


def _seed_admin(Session):
    db = Session()
    try:
        db.add(User(id=1, name="Admin", username="admin", password_hash="x", role=UserRole.ADMIN))
        db.commit()
    finally:
        db.close()


def test_account_fields_cover_required_accounts():
    keys = {f["key"] for f in QBO_ACCOUNT_FIELDS}
    assert {"qbo_bill_expense_account", "qbo_income_account",
            "qbo_inventory_asset_account", "qbo_freight_in_account",
            "qbo_freight_out_account"} <= keys


def test_account_mappings_default_blank(Session):
    db = Session()
    try:
        m = QBOSyncService(db).account_mappings()
        assert m["qbo_inventory_asset_account"] == ""
        assert set(m.keys()) == {f["key"] for f in QBO_ACCOUNT_FIELDS}
    finally:
        db.close()


def test_list_accounts_fails_soft_when_disconnected(Session):
    db = Session()
    try:
        res = QBOSyncService(db).list_accounts()
        assert res["ok"] is False and res["error"]   # no exception, friendly error
    finally:
        db.close()


def test_qbo_accounts_page_renders_disconnected(Session, client):
    _seed_admin(Session)
    r = client.get("/settings/qbo-accounts")
    assert r.status_code == 200
    assert "isn't connected" in r.text or "QuickBooks settings" in r.text


def test_qbo_accounts_save_persists(Session, client):
    _seed_admin(Session)
    r = client.post("/settings/qbo-accounts", data={
        "qbo_bill_expense_account": "Cost of Goods Sold",
        "qbo_income_account": "Sales of Product Income",
        "qbo_inventory_asset_account": "Inventory Asset",
        "qbo_freight_in_account": "Freight & Shipping Costs",
        "qbo_freight_out_account": "Shipping Income",
    }, follow_redirects=False)
    assert r.status_code == 303
    db = Session()
    try:
        m = QBOSyncService(db).account_mappings()
        assert m["qbo_bill_expense_account"] == "Cost of Goods Sold"
        assert m["qbo_inventory_asset_account"] == "Inventory Asset"
        assert m["qbo_freight_out_account"] == "Shipping Income"
    finally:
        db.close()
