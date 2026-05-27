"""QBO CSV variants — choose which importer's header convention the CSV
writers should target.

Background
----------
QuickBooks Online's *native* Gear → Tools → Import Data wizard only
supports **Invoices** out of the box; Bills, Receive-Payments, and
Credit Memos require a third-party importer (SaasAnt Transactions,
Transaction Pro Importer, Business Importer, etc.). Each importer uses
its own column-header conventions.

To avoid the trap of "I exported the file and QBO rejected it", users
can pick a variant per export. The writers in :mod:`qbo.csv_export`
consult this module for column names; the data columns themselves are
in the same order across variants — only the header strings change.

The :data:`SUPPORTED_NATIVELY` map tells the UI which (variant, entity)
combinations the *official* QBO Online importer accepts; the UI uses
this to surface a banner when the user picks a non-native combo.
"""
from __future__ import annotations

from enum import Enum


class CsvVariant(str, Enum):
    """Importer dialect for the exported CSV.

    Stored in ``app_settings`` under the key
    :data:`SETTINGS_KEY` and resolved by
    :func:`get_default_variant`.
    """

    GENERIC = "generic"
    QBO_ONLINE = "qbo_online"
    SAASANT = "saasant"
    TRANSACTION_PRO = "transaction_pro"


SETTINGS_KEY = "qbo_csv_variant"


# Friendly labels for the dropdown.
VARIANT_LABELS: dict[CsvVariant, str] = {
    CsvVariant.GENERIC: "Generic (legacy)",
    CsvVariant.QBO_ONLINE: "QuickBooks Online (native importer)",
    CsvVariant.SAASANT: "SaasAnt Transactions",
    CsvVariant.TRANSACTION_PRO: "Transaction Pro Importer",
}


# Per-entity, per-variant header rows. The column COUNT and ORDER must
# stay aligned with the data rows emitted by :mod:`qbo.csv_export`.
#
# Generic baseline (must match the constants in qbo.csv_export):
#   invoice: 12 cols
#   bill:     9 cols
#   payment:  7 cols
#   credit:  11 cols

INVOICE_HEADERS: dict[CsvVariant, list[str]] = {
    CsvVariant.GENERIC: [
        "InvoiceNo", "Date", "DueDate", "Customer",
        "Item", "Description", "Quantity", "Rate", "Amount",
        "TaxCode", "TaxAmount", "Memo",
    ],
    # Headers per the QuickBooks Online Invoice CSV importer
    # (Gear → Tools → Import Data → Invoices). The native wizard ignores
    # unknown columns, so trailing "Memo" is tolerated.
    CsvVariant.QBO_ONLINE: [
        "InvoiceNo", "InvoiceDate", "DueDate", "Customer",
        "Product/Service", "ItemDescription", "ItemQuantity",
        "ItemRate", "ItemAmount",
        "Item Tax Code", "Item Tax Amount", "Memo",
    ],
    # SaasAnt Transactions — Invoice template column names.
    CsvVariant.SAASANT: [
        "InvoiceNo", "InvoiceDate", "DueDate", "Customer",
        "Item(Product/Service)", "Description", "Quantity",
        "Rate", "Amount",
        "TaxCode", "TaxAmount", "Memo",
    ],
    # Transaction Pro Importer.
    CsvVariant.TRANSACTION_PRO: [
        "RefNumber", "TxnDate", "DueDate", "CustomerRef",
        "ItemRef", "Description", "Quantity", "Rate", "Amount",
        "TaxCodeRef", "SalesTaxAmount", "Memo",
    ],
}

BILL_HEADERS: dict[CsvVariant, list[str]] = {
    CsvVariant.GENERIC: [
        "BillNo", "Date", "Vendor",
        "Item", "Description", "Quantity", "Cost", "Amount", "Memo",
    ],
    # Bills require a 3rd-party importer on QBO Online; we still emit
    # header names that look correct so the SaasAnt mapping step is
    # one-click.
    CsvVariant.QBO_ONLINE: [
        "BillNo", "BillDate", "Vendor",
        "Product/Service", "ItemDescription", "ItemQuantity",
        "ItemRate", "ItemAmount", "Memo",
    ],
    CsvVariant.SAASANT: [
        "BillNo", "BillDate", "Vendor",
        "Item(Product/Service)", "Description", "Quantity",
        "Cost", "Amount", "Memo",
    ],
    CsvVariant.TRANSACTION_PRO: [
        "RefNumber", "TxnDate", "VendorRef",
        "ItemRef", "Description", "Quantity", "Cost", "Amount", "Memo",
    ],
}

PAYMENT_HEADERS: dict[CsvVariant, list[str]] = {
    CsvVariant.GENERIC: [
        "PaymentNo", "Date", "Customer", "InvoiceNo",
        "Amount", "PaymentMethod", "Memo",
    ],
    CsvVariant.QBO_ONLINE: [
        "PaymentNo", "PaymentDate", "Customer", "InvoiceNo",
        "Amount", "PaymentMethod", "Memo",
    ],
    CsvVariant.SAASANT: [
        "PaymentNo", "PaymentDate", "Customer", "InvoiceNo",
        "Amount", "PaymentMethod", "Memo",
    ],
    CsvVariant.TRANSACTION_PRO: [
        "RefNumber", "TxnDate", "CustomerRef", "AppliesToTxnRef",
        "Amount", "PaymentMethodRef", "Memo",
    ],
}

CREDIT_HEADERS: dict[CsvVariant, list[str]] = {
    CsvVariant.GENERIC: [
        "CreditMemoNo", "Date", "Customer",
        "Item", "Description", "Quantity", "Rate", "Amount",
        "Status", "AppliedToInvoice", "Memo",
    ],
    CsvVariant.QBO_ONLINE: [
        "CreditMemoNo", "CreditMemoDate", "Customer",
        "Product/Service", "ItemDescription", "ItemQuantity",
        "ItemRate", "ItemAmount",
        "Status", "AppliedToInvoice", "Memo",
    ],
    CsvVariant.SAASANT: [
        "CreditMemoNo", "CreditMemoDate", "Customer",
        "Item(Product/Service)", "Description", "Quantity",
        "Rate", "Amount",
        "Status", "AppliedToInvoice", "Memo",
    ],
    CsvVariant.TRANSACTION_PRO: [
        "RefNumber", "TxnDate", "CustomerRef",
        "ItemRef", "Description", "Quantity", "Rate", "Amount",
        "Status", "AppliesToTxnRef", "Memo",
    ],
}


# Native QBO Online importer support matrix. The Gear → Tools → Import
# Data wizard only accepts the entities flagged True here; everything
# else requires a third-party importer regardless of variant.
SUPPORTED_NATIVELY: dict[str, bool] = {
    "invoice": True,
    "bill": False,
    "payment": False,
    "credit": False,
}


def get_headers(variant: "CsvVariant | str | None", entity: str) -> list[str]:
    """Return the header row for the requested entity/variant.

    ``entity`` is one of ``"invoice"``, ``"bill"``, ``"payment"``,
    ``"credit"``. Unknown variants fall back to :data:`CsvVariant.GENERIC`.
    """
    v = _coerce(variant)
    table = {
        "invoice": INVOICE_HEADERS,
        "bill": BILL_HEADERS,
        "payment": PAYMENT_HEADERS,
        "credit": CREDIT_HEADERS,
    }.get(entity)
    if table is None:
        raise ValueError(f"Unknown entity: {entity!r}")
    return list(table.get(v, table[CsvVariant.GENERIC]))


def _coerce(variant) -> CsvVariant:
    if isinstance(variant, CsvVariant):
        return variant
    if variant is None:
        return CsvVariant.GENERIC
    try:
        return CsvVariant(str(variant).strip().lower())
    except ValueError:
        return CsvVariant.GENERIC


def get_default_variant() -> CsvVariant:
    """Return the user's preferred variant from ``app_settings`` (or
    :data:`CsvVariant.GENERIC` when unset / on any error)."""
    try:
        from db import get_setting  # local import — keeps tests fast
        raw = get_setting(SETTINGS_KEY)
    except Exception:
        raw = None
    return _coerce(raw)


def set_default_variant(variant: "CsvVariant | str") -> bool:
    """Persist the user's preferred variant."""
    v = _coerce(variant)
    try:
        from db import set_setting
        set_setting(SETTINGS_KEY, v.value)
        return True
    except Exception:
        return False


def native_support_note(variant: "CsvVariant | str | None") -> str:
    """Human-readable banner text describing which entities the chosen
    variant can hand off to a native QBO Online import vs. needing a
    third-party importer. The note is the same for every non-native
    variant because QBO Online itself only natively ingests Invoices.
    """
    v = _coerce(variant)
    if v == CsvVariant.QBO_ONLINE:
        return (
            "QuickBooks Online natively imports Invoices only. "
            "Bills, Customer Payments, and Credit Memos require a "
            "third-party importer (SaasAnt, Transaction Pro, Business "
            "Importer) or manual entry."
        )
    if v == CsvVariant.SAASANT:
        return (
            "SaasAnt Transactions can import all four document types. "
            "Map each CSV through SaasAnt's import wizard after export."
        )
    if v == CsvVariant.TRANSACTION_PRO:
        return (
            "Transaction Pro Importer can import all four document "
            "types. Use TPI's mapping screen after export."
        )
    return (
        "Generic CSV (legacy). Use this when the file will be hand-keyed "
        "or fed to a custom script. QBO Online's native importer expects "
        "different headers — switch to the 'QuickBooks Online' variant "
        "for the native Invoice import."
    )
