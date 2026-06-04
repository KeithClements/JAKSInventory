"""
app/models/__init__.py
======================
Import every model class so SQLAlchemy's Base.metadata.create_all()
picks up all tables in a single call from app/database.py.

Import ORDER matters for models with forward references.
Leaf models (no FK dependencies) first; junction/child tables last.
"""

# ── Foundation ────────────────────────────────────────────────────────────────
from app.models.setting import Setting
from app.models.user import User, UserSession
from app.models.audit import AuditLog

# ── Master data ───────────────────────────────────────────────────────────────
from app.models.vendor import Vendor, VendorContact, VendorCredit, VendorProgram
from app.models.customer import (
    Customer, CustomerAddress, CustomerContact, CustomerCallLog, CustomerTypeDefault,
)
from app.models.product import (
    ProductCategory,
    Product,
    ProductVendorSource,
    ProductImage,
    CrossReference,
    ProductApplication,
    ProductCostHistory,
    ProductSerialNumber,
    ProductKit,
    ProductKitLine,
)

# ── Inventory ─────────────────────────────────────────────────────────────────
from app.models.inventory import InventoryLocation, InventoryTransaction

# ── Purchasing ────────────────────────────────────────────────────────────────
from app.models.purchase_order import (
    PurchaseOrder,
    POLine,
    POReceipt,
    POReceiptLine,
    VendorBill,
    VendorBillLine,
)

# ── Research ──────────────────────────────────────────────────────────────────
from app.models.research import ResearchItem, ResearchActivityLog

# ── Sales cycle ───────────────────────────────────────────────────────────────
from app.models.quote import (
    Quote,
    QuoteLine,
    SalesOrder,
    SOLine,
    QuoteFollowup,
    LostSaleLog,
)
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation

# ── Special processes ─────────────────────────────────────────────────────────
from app.models.core import (
    CoreCharge,
    CoreReturnEvent,
    CoreLocation,
    CoreLocationMovement,
    CoreSlip,
    VendorCoreReturn,
    VendorCoreReturnLine,
)
from app.models.returns import ReturnAuthorization, ReturnLine
from app.models.warranty import (
    WarrantyClaim,
    WarrantyClaimLine,
    ESNLookup,
    EngineConfig,
)

# ── Credit Memos + Vendor Credits + Returns (R8, R11) ────────────────────────
from app.models.credit_memo import CreditMemo, CreditMemoLine, CreditMemoAllocation
from app.models.vendor_credit import VendorCreditMemo, VendorCreditMemoAllocation
from app.models.vendor_return import VendorReturn, VendorReturnLine

# ── Statements + Notifications + Communications (R8, R12) ────────────────────
from app.models.statement import CustomerStatement
from app.models.notification import Notification
from app.models.communication import Communication, CommunicationAttachment

# ── Inventory transfers (R6) ──────────────────────────────────────────────────
from app.models.inventory_transfer import InventoryTransfer

# ── Supporting ────────────────────────────────────────────────────────────────
from app.models.shipping import Shipment
from app.models.attachments import DocumentAttachment

# ── Exposed to init_db() ──────────────────────────────────────────────────────
__all_models__ = [
    # Foundation
    Setting,
    User,
    UserSession,
    AuditLog,
    # Master data
    Vendor,
    VendorContact,
    VendorCredit,
    VendorProgram,
    Customer,
    CustomerAddress,
    CustomerContact,
    CustomerCallLog,
    CustomerTypeDefault,
    ProductCategory,
    Product,
    ProductVendorSource,
    ProductImage,
    CrossReference,
    ProductApplication,
    ProductCostHistory,
    ProductSerialNumber,
    ProductKit,
    ProductKitLine,
    # Inventory
    InventoryLocation,
    InventoryTransaction,
    # Purchasing
    PurchaseOrder,
    POLine,
    POReceipt,
    POReceiptLine,
    VendorBill,
    VendorBillLine,
    # Sales cycle
    Quote,
    QuoteLine,
    SalesOrder,
    SOLine,
    QuoteFollowup,
    LostSaleLog,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentAllocation,
    # Research
    ResearchItem,
    ResearchActivityLog,
    # Special processes
    CoreCharge,
    CoreReturnEvent,
    CoreLocation,
    CoreLocationMovement,
    CoreSlip,
    VendorCoreReturn,
    VendorCoreReturnLine,
    ReturnAuthorization,
    ReturnLine,
    WarrantyClaim,
    WarrantyClaimLine,
    ESNLookup,
    EngineConfig,
    # Credit memos + vendor credits + returns
    CreditMemo,
    CreditMemoLine,
    CreditMemoAllocation,
    VendorCreditMemo,
    VendorCreditMemoAllocation,
    VendorReturn,
    VendorReturnLine,
    # Statements + Notifications + Communications
    CustomerStatement,
    Notification,
    Communication,
    CommunicationAttachment,
    # Inventory transfers
    InventoryTransfer,
    # Supporting
    Shipment,
    DocumentAttachment,
]
