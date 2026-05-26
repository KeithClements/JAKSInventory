"""
app/services/__init__.py
========================
Service layer — all business logic lives here.
Routers are thin shells; services are the ERP.

Import pattern in routers:
    from app.services.invoice_service import InvoiceService
    svc = InvoiceService(db)
    result = svc.create_invoice(...)
"""
