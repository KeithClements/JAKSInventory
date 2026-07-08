"""
tests/test_phase3_warranty_esn_search_replacement.py
====================================================
§23.3 Phase 3 — warranty search by ESN + replacement-parts tracking.

  (1) The Warranty Queue search matches the claim-level ESN and per-line
      failed-part serials, and a search INCLUDES closed claims (an engine's
      warranty history outlives the work queue) while the unsearched queue
      still excludes them.
  (2) update_service_info() accepts replacement_invoice_number per line and
      resolves it to that invoice's product line for the SAME product,
      storing replacement_invoice_line_id (the previously-dormant column).
      Blank clears; unknown invoice / wrong product raise.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


_SEQ = {"n": 0}


def _uniq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"P3 WES Cust {_uniq()}")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"P3 WES Vendor {_uniq()}")
    db.add(v); db.commit(); db.refresh(v)
    return v


def _product(db):
    from app.constants import ProductStatus
    from app.models.product import Product
    p = Product(sku=f"P3WES-{_uniq():04d}", title="Head", description="Head",
                cost=100.0, markup_pct=50.0, qty_on_hand=1,
                status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _claim(db, *, esn="", line_serial=None, product=None):
    from app.services.warranty_service import WarrantyService
    cust = _customer(db); ven = _vendor(db)
    prod = product or _product(db)
    line = {"product_id": prod.id, "qty_claimed": 1, "credit_amount": 0.0}
    if line_serial:
        line["serial_number"] = line_serial
    return WarrantyService(db, None).create_claim(
        customer_id=cust.id, invoice_id=None, vendor_id=ven.id,
        failure_description="failed", lines=[line], esn=esn,
    )


def _invoice_with_line(db, *, product, invoice_number=None):
    from app.constants import InvoiceStatus, LineType
    from app.models.invoice import Invoice, InvoiceLine
    num = invoice_number or f"INV-P3WES-{_uniq():05d}"
    inv = Invoice(invoice_number=num, customer_id=_customer(db).id,
                  status=InvoiceStatus.OPEN, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    db.add(inv); db.flush()
    line = InvoiceLine(invoice_id=inv.id, line_type=LineType.PRODUCT,
                       product_id=product.id, description=product.title,
                       qty=1, unit_price=200.0, unit_cost=100.0,
                       is_taxable=False)
    db.add(line); db.commit(); db.refresh(inv); db.refresh(line)
    return inv, line


# ===========================================================================
# (1) Search by ESN / line serial, including closed claims
# ===========================================================================

def test_search_matches_claim_esn(client, db):
    claim = _claim(db, esn=f"ESN-79R-{_uniq()}")
    r = client.get(f"/warranty/?q={claim.esn}")
    assert r.status_code == 200
    assert claim.claim_number in r.text


def test_search_matches_line_serial(client, db):
    serial = f"HEAD-SN-{_uniq()}"
    claim = _claim(db, esn="ESN-X", line_serial=serial)
    r = client.get(f"/warranty/?q={serial}")
    assert r.status_code == 200
    assert claim.claim_number in r.text


def test_search_includes_closed_claims_queue_does_not(client, db):
    from app.constants import WarrantyStatus
    esn = f"ESN-CLOSED-{_uniq()}"
    claim = _claim(db, esn=esn)
    claim.status = WarrantyStatus.CLOSED
    db.commit()

    # The plain queue excludes closed claims
    queue = client.get("/warranty/")
    assert queue.status_code == 200
    assert claim.claim_number not in queue.text

    # ...but an ESN search finds the history, rendered with the Closed chip
    found = client.get(f"/warranty/?q={esn}")
    assert found.status_code == 200
    assert claim.claim_number in found.text
    assert "Closed" in found.text


def test_search_no_match_renders_empty(client, db):
    r = client.get("/warranty/?q=ZZZ-NO-SUCH-ESN-ZZZ")
    assert r.status_code == 200


# ===========================================================================
# (2) Replacement-parts tracking
# ===========================================================================

def test_replacement_link_resolves_to_matching_product_line(db):
    from app.models.warranty import WarrantyClaimLine
    from app.services.warranty_service import WarrantyService
    prod = _product(db)
    claim = _claim(db, esn="ESN-R1", product=prod)
    inv, inv_line = _invoice_with_line(db, product=prod)

    line_id = claim.claim_lines[0].id
    WarrantyService(db, None).update_service_info(
        claim.id, {line_id: {"replacement_invoice_number": inv.invoice_number}})

    db.expire_all()
    cl = db.query(WarrantyClaimLine).filter(WarrantyClaimLine.id == line_id).first()
    assert cl.replacement_invoice_line_id == inv_line.id
    # relationship resolves back to the invoice
    assert cl.replacement_invoice_line.invoice.invoice_number == inv.invoice_number


def test_replacement_link_blank_clears(db):
    from app.models.warranty import WarrantyClaimLine
    from app.services.warranty_service import WarrantyService
    prod = _product(db)
    claim = _claim(db, esn="ESN-R2", product=prod)
    inv, _ = _invoice_with_line(db, product=prod)
    line_id = claim.claim_lines[0].id
    svc = WarrantyService(db, None)
    svc.update_service_info(
        claim.id, {line_id: {"replacement_invoice_number": inv.invoice_number}})
    svc.update_service_info(
        claim.id, {line_id: {"replacement_invoice_number": ""}})

    db.expire_all()
    cl = db.query(WarrantyClaimLine).filter(WarrantyClaimLine.id == line_id).first()
    assert cl.replacement_invoice_line_id is None


def test_replacement_link_unknown_invoice_raises(db):
    from app.services.warranty_service import WarrantyService
    claim = _claim(db, esn="ESN-R3")
    line_id = claim.claim_lines[0].id
    with pytest.raises(ValueError, match="not found"):
        WarrantyService(db, None).update_service_info(
            claim.id, {line_id: {"replacement_invoice_number": "INV-NOPE-99999"}})
    db.rollback()


def test_replacement_link_wrong_product_raises(db):
    from app.services.warranty_service import WarrantyService
    claim = _claim(db, esn="ESN-R4", product=_product(db))
    other_inv, _ = _invoice_with_line(db, product=_product(db))  # different part
    line_id = claim.claim_lines[0].id
    with pytest.raises(ValueError, match="no product line"):
        WarrantyService(db, None).update_service_info(
            claim.id, {line_id: {"replacement_invoice_number": other_inv.invoice_number}})
    db.rollback()


def test_replacement_link_via_route(client, db):
    from app.models.warranty import WarrantyClaimLine
    prod = _product(db)
    claim = _claim(db, esn="ESN-R5", product=prod)
    inv, inv_line = _invoice_with_line(db, product=prod)
    line_id = claim.claim_lines[0].id

    r = client.post(
        f"/warranty/{claim.id}/service-info",
        data={
            "line_id[]": [str(line_id)],
            "serial_number[]": ["SN-ROUTE-1"],
            "labor_hours[]": ["1.5"],
            "labor_rate[]": ["95"],
            "replacement_invoice[]": [inv.invoice_number],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and "error" not in r.headers["location"]

    db.expire_all()
    cl = db.query(WarrantyClaimLine).filter(WarrantyClaimLine.id == line_id).first()
    assert cl.replacement_invoice_line_id == inv_line.id
    assert cl.serial_number == "SN-ROUTE-1"
    assert cl.labor_hours == 1.5 and cl.labor_rate == 95.0

    # Workspace shows the linked invoice number in the service-info form
    page = client.get(f"/warranty/{claim.id}")
    assert page.status_code == 200
    assert inv.invoice_number in page.text
