"""
tests/test_s21_warranty_interest.py
===================================
§21.10 — setting-gated warranty ESN guard + post-accrued-interest-as-draft-invoice.
"""
from __future__ import annotations

import datetime as _dt
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine
from app.models import __all_models__  # noqa: F401

import pytest

from app.constants import InvoiceStatus, LineType, WarrantyStatus, WarrantyType
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.vendor import Vendor
from app.services.crm_service import CRMService
from app.services.warranty_service import WarrantyService
from app.settings_utils import set_setting_value_db

_ENGINE = fresh_engine()
_UID = None
_seq = itertools.count(1)


@pytest.fixture()
def db():
    activate(_ENGINE)
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Warranty ESN gate (setting-driven) ────────────────────────────────────────

def _vendor_claim_no_esn(db):
    v = Vendor(name=f"WV {next(_seq)}")
    cust = Customer(company_name=f"WC {next(_seq)}")
    db.add_all([v, cust]); db.commit(); db.refresh(v); db.refresh(cust)
    svc = WarrantyService(db, _UID)
    claim = svc.create_claim(
        customer_id=cust.id, invoice_id=None, vendor_id=v.id,
        failure_description="bad part", lines=[{"description": "x", "qty": 1}],
        warranty_type=WarrantyType.VENDOR, esn="")
    return svc, claim


def test_warranty_esn_not_required_by_default(db):
    svc, claim = _vendor_claim_no_esn(db)
    svc.submit_to_vendor(claim.id)   # default setting off → allowed
    db.expire_all()
    from app.models.warranty import WarrantyClaim
    assert db.query(WarrantyClaim).get(claim.id).status == WarrantyStatus.SUBMITTED_TO_VENDOR


def test_warranty_esn_required_when_setting_on(db):
    set_setting_value_db(db, "warranty_require_esn", "true")
    db.commit()
    svc, claim = _vendor_claim_no_esn(db)
    with pytest.raises(ValueError, match="ESN"):
        svc.submit_to_vendor(claim.id)
    set_setting_value_db(db, "warranty_require_esn", "false")  # reset for isolation
    db.commit()


# ── Post accrued interest as a draft invoice ──────────────────────────────────

def _overdue_customer(db, rate=18.0) -> Customer:
    c = Customer(company_name=f"Int Co {next(_seq)}", interest_rate=rate,
                 interest_grace_days=0)
    db.add(c); db.commit(); db.refresh(c)
    # An overdue, unpaid invoice so there's a balance to accrue on.
    due = _dt.datetime.utcnow() - _dt.timedelta(days=120)
    inv = Invoice(invoice_number=f"INV-INT-{next(_seq):05d}", customer_id=c.id,
                  status=InvoiceStatus.OPEN, due_date=due, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT, description="p",
                       qty=1, unit_price=1000.0, unit_cost=600.0, is_taxable=False))
    db.commit()
    return c


def test_post_interest_creates_draft_finance_charge(db):
    c = _overdue_customer(db)
    crm = CRMService(db, _UID)
    accrued = crm.calculate_interest_charge(c.id)
    assert accrued > 0

    inv = crm.post_interest_charge(c.id)
    db.expire_all()
    inv = db.query(Invoice).get(inv.id)
    assert inv.status == InvoiceStatus.DRAFT          # never auto-posted
    fee_lines = [ln for ln in inv.lines if ln.line_type == LineType.MISC_FEE]
    assert len(fee_lines) == 1
    assert fee_lines[0].unit_price == pytest.approx(accrued)


def test_post_interest_raises_when_nothing_accrued(db):
    c = Customer(company_name=f"No Int {next(_seq)}", interest_rate=0.0)
    db.add(c); db.commit(); db.refresh(c)
    with pytest.raises(ValueError, match="No accrued interest"):
        CRMService(db, _UID).post_interest_charge(c.id)
