"""
tests/visual/screens.py
=======================
Screen registry for visual regression testing.

Each ScreenVariant drives:
  - capture_baselines.py  (first-run PNG capture)
  - test_visual_regression.py  (ongoing pixel diff)

States covered:
  normal      -- default list/page view (seeded data)
  empty_search -- ?q=zzz_no_match forces 0 results, shows empty-search state
  empty_tab   -- a tab that has 0 records in a clean DB (shows tab-filter state)
  error       -- 404 / intentional bad route to test error rendering
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ScreenVariant:
    label: str          # used as filename stem: {label}@{vp}px.png
    url: str
    wait_for: str = "networkidle"   # playwright wait_until strategy
    # Extra ms to wait after network idle (for Alpine hydration / CSS paint)
    settle_ms: int = 600


# ---------------------------------------------------------------------------
# Screen registry -- ordered by rollout priority
# ---------------------------------------------------------------------------

SCREENS: list[ScreenVariant] = [

    # ── Reference L2 list screens ──────────────────────────────────────────

    ScreenVariant("products_list",          "/products/"),
    ScreenVariant("products_list_empty",    "/products/?q=zzz_no_match_xyz"),
    ScreenVariant("products_outofstock_tab","/products/?tab=out_of_stock"),

    ScreenVariant("invoices_list",          "/invoices/"),
    ScreenVariant("invoices_list_empty",    "/invoices/?q=zzz_no_match_xyz"),
    ScreenVariant("invoices_overdue_tab",   "/invoices/?tab=overdue"),
    ScreenVariant("invoices_paid_tab",      "/invoices/?tab=paid"),

    ScreenVariant("purchase_orders_list",           "/purchase-orders/"),
    ScreenVariant("purchase_orders_list_empty",     "/purchase-orders/?q=zzz_no_match_xyz"),
    ScreenVariant("purchase_orders_overdue_tab",    "/purchase-orders/?tab=overdue"),

    ScreenVariant("customers_list",         "/customers/"),
    ScreenVariant("customers_list_empty",   "/customers/?q=zzz_no_match_xyz"),

    ScreenVariant("quotes_list",            "/quotes/"),
    ScreenVariant("quotes_list_empty",      "/quotes/?q=zzz_no_match_xyz"),

    # ── L1 screens (flagged by lint gate, not yet L2) ─────────────────────

    ScreenVariant("sales_orders_list",      "/sales-orders/"),
    ScreenVariant("vendors_list",           "/vendors/"),
    ScreenVariant("payments_list",          "/payments/"),
    ScreenVariant("warranty_list",          "/warranty/"),
    ScreenVariant("returns_list",           "/returns/"),
    ScreenVariant("cores_list",             "/cores/"),

    # ── Queue boards (§2A) ────────────────────────────────────────────────

    ScreenVariant("po_receiving_queue",     "/purchase-orders/receiving"),
    ScreenVariant("po_match_queue",         "/purchase-orders/match"),

    # ── Other key screens ─────────────────────────────────────────────────

    ScreenVariant("dashboard",              "/"),
    ScreenVariant("products_new",           "/products/new"),
    ScreenVariant("customers_new",          "/customers/new"),
    ScreenVariant("purchase_orders_new",    "/purchase-orders/new"),
    ScreenVariant("quotes_new",             "/quotes/new"),
    ScreenVariant("invoices_new",           "/invoices/new"),
]

# Viewports used for every screen
VIEWPORTS = [
    {"label": "1280", "width": 1280, "height": 800},
    {"label": "1920", "width": 1920, "height": 1080},
]
