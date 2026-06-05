"""
tests/test_customer_preview_metrics.py
======================================
Preview-dock metric context (§4.4 / P2-Q1). The customer bottom-preview panel
(GET /customers/preview/{id} → customers/_preview_panel.html →
intel.customer_intelligence_panel(metrics)) must show the SAME relationship
numbers the customer LIST and DETAIL panels use — there is one definition
(CustomerMetricsService), so preview (metrics_for) and list (metrics_for_batch)
can never disagree, and the panel actually renders those values.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_customer_preview_metrics.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import InvoiceStatus, LineType
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.services.customer_metrics_service import CustomerMetricsService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _customer(db) -> Customer:
    c = Customer(company_name=f"Preview Co {next(_seq)}", contact_name="QA")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer_id, amount) -> Invoice:
    inv = Invoice(invoice_number=f"INV-PV-{next(_seq):05d}", customer_id=customer_id,
                  status=InvoiceStatus.OPEN, is_taxable=False, tax_rate=0.0,
                  tax_rate_snapshot=0.0, tax_exempt_snapshot=True)
    inv.lines.append(InvoiceLine(line_type=LineType.PRODUCT, qty=1, unit_price=amount,
                                 unit_cost=0.0, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def test_preview_metrics_match_service_and_render(client, db):
    cust = _customer(db)
    _open_invoice(db, cust.id, 500.0)   # one OPEN invoice → lifetime 500, open_ar 500

    svc = CustomerMetricsService(db)
    single = svc.metrics_for(cust.id)            # the value the preview panel uses
    batch = svc.metrics_for_batch([cust.id])[cust.id]   # the value the LIST uses
    # one definition → preview and list can never disagree
    assert single == batch
    assert single["lifetime_sales"] == 500.0 and single["open_ar"] == 500.0

    r = client.get(f"/customers/preview/{cust.id}")
    assert r.status_code == 200
    # the intelligence panel actually renders the metric values in the dock
    assert "500.00" in r.text


def test_preview_renders_for_zero_activity_customer(client, db):
    cust = _customer(db)   # no invoices → all-zero metrics, panel still renders
    r = client.get(f"/customers/preview/{cust.id}")
    assert r.status_code == 200
    assert CustomerMetricsService(db).metrics_for(cust.id)["lifetime_sales"] == 0.0
