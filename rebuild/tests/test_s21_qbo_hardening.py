"""
tests/test_s21_qbo_hardening.py
===============================
§21.10 — QBO push hardening: pending-only bulk sync (skip ERROR), background
retry worker queue, AST-tax error detection, persisted Vendor.qbo_vendor_id.
(Live-network paths — 429 backoff, the actual AST no-override retry — aren't
exercised here; the queue/detection logic that drives them is.)
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

import pytest

from app.constants import InvoiceStatus, QBOSyncStatus
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.services.qbo_service import QBOSyncService, _is_ast_tax_error
from app.services.qbo_client import QBOError

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture()
def db():
    activate(_ENGINE)
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _inv(db, *, sync_status, retry=0) -> Invoice:
    c = Customer(company_name=f"QBO Co {next(_seq)}")
    db.add(c); db.flush()
    inv = Invoice(invoice_number=f"INV-QBO-{next(_seq):05d}", customer_id=c.id,
                  status=InvoiceStatus.OPEN, qbo_sync_status=sync_status,
                  qbo_sync_retry_count=retry)
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def test_vendor_has_qbo_vendor_id_column(db):
    v = Vendor(name=f"V{next(_seq)}", qbo_vendor_id="123")
    db.add(v); db.commit(); db.refresh(v)
    assert v.qbo_vendor_id == "123"


def test_pending_only_excludes_error(db):
    p = _inv(db, sync_status=QBOSyncStatus.PENDING)
    e = _inv(db, sync_status=QBOSyncStatus.ERROR)
    _inv(db, sync_status=QBOSyncStatus.SYNCED)   # never returned
    svc = QBOSyncService(db, current_user_id=None)

    all_unsynced = set(svc.unsynced_invoice_ids())
    pending = set(svc.unsynced_invoice_ids(pending_only=True))
    assert p.id in all_unsynced and e.id in all_unsynced     # default: both
    assert p.id in pending and e.id not in pending           # pending_only: skips ERROR


def test_failed_invoice_ids_respects_retry_ceiling(db):
    under = _inv(db, sync_status=QBOSyncStatus.ERROR, retry=2)
    over = _inv(db, sync_status=QBOSyncStatus.ERROR, retry=9)   # past ceiling
    ids = set(QBOSyncService(db, current_user_id=None).failed_invoice_ids(max_retries=5))
    assert under.id in ids and over.id not in ids


def test_ast_tax_error_detection():
    assert _is_ast_tax_error(QBOError("400: You cannot specify the Txn Tax Detail ..."))
    assert _is_ast_tax_error(QBOError("Automated Sales Tax is enabled"))
    assert not _is_ast_tax_error(QBOError("Customer not found"))
    assert not _is_ast_tax_error(QBOError("network timeout"))
