"""
app/services/shopify_order_sync.py
==================================
Pull Shopify web orders into the ERP so a web sale DECREMENTS ERP stock — the
missing half of the sync (the ERP already pushes price/stock/availability OUT, but
nothing came back IN, so a counter sale and a web sale could both take the last
unit). This is mandatory before tracking real shelf inventory.

Design:
  • The ERP runs on the owner's PC (no public URL), so we POLL the Orders API on a
    watermark instead of receiving webhooks. Cadence + on/off are settings.
  • IDEMPOTENT: every order is recorded once in shopify_processed_orders; a re-poll
    of the same window (or an at-least-once scheduler tick) never double-decrements.
    All of an order's line decrements + its processed marker commit in ONE
    transaction, so idempotency is atomic.
  • PACK-AWARE: one storefront unit is one vendor sell pack, so pieces decremented
    = line quantity × product.pack_qty (mirrors the outbound listing math).
  • Fail-soft: an unreadable order / unmatched SKU is recorded, never raised. The
    watermark only advances past orders handled without error, so a transient
    failure is retried on the next poll (never skipped).

Scope note: the ERP custom-app token needs read_orders. If it lacks that scope,
poll() returns a clear scope_error telling the owner to add it and reinstall.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.models.product import Product, ProductVendorSource
from app.models.shopify_order import ShopifyProcessedOrder
from app.services.base import BaseService
from app.services.inventory_service import InventoryService
from app.services.shopify_service import ShopifyService
from app.settings_utils import get_setting_value_db, set_setting_value_db

_ORDERS_QUERY = (
    "query($cursor: String, $q: String) {"
    "  orders(first: 40, after: $cursor, query: $q, sortKey: CREATED_AT, reverse: false) {"
    "    pageInfo { hasNextPage endCursor }"
    "    nodes { id name createdAt cancelledAt"
    "      lineItems(first: 100) { nodes { quantity sku variant { id } } } } } }"
)


class ShopifyOrderSyncService(BaseService):
    """Poll Shopify orders → decrement ERP stock (idempotent, pack-aware)."""

    def __init__(self, db, user_id=None):
        super().__init__(db, user_id)
        self._shop = ShopifyService(db, user_id)

    def is_configured(self) -> bool:
        return self._shop.is_configured()

    @staticmethod
    def _numeric(gid: str) -> str:
        return (gid or "").rsplit("/", 1)[-1].strip()

    def _match_product(self, sku: str, variant_gid: str) -> Product | None:
        """Resolve an order line to an ERP product: linked variant GID first (the
        strongest key), then the master SKU, then a vendor-source SKU."""
        sku = (sku or "").strip()
        vid = (variant_gid or "").strip()
        if vid.startswith("gid://"):
            p = self.db.query(Product).filter(Product.shopify_variant_id == vid).first()
            if p:
                return p
        if sku:
            p = self.db.query(Product).filter(Product.sku == sku).first()
            if p:
                return p
            src = (self.db.query(ProductVendorSource)
                   .filter(ProductVendorSource.vendor_sku == sku).first())
            if src:
                return self.db.query(Product).filter(Product.id == src.product_id).first()
        return None

    def _reverse_order(self, processed: ShopifyProcessedOrder) -> int:
        """Restock a previously-decremented order that Shopify now reports cancelled.
        Restocks each matched line from the stored detail, then stamps reversed_at so
        it can only ever restock once. Commits (its own atomic unit). Returns pieces
        restored (0 if the detail can't be read or nothing to restore)."""
        try:
            detail = json.loads(processed.detail or "[]")
        except (ValueError, TypeError):
            detail = []
        inv = InventoryService(self.db, self.current_user_id)
        restored = 0
        for d in detail:
            if d.get("matched") and d.get("product_id") and d.get("pieces"):
                inv.record_shopify_order_reversal(
                    int(d["product_id"]), int(d["pieces"]),
                    order_ref_id=self._numeric(processed.shopify_order_id),
                    order_name=processed.order_name)
                restored += int(d["pieces"])
        processed.reversed_at = datetime.utcnow()
        try:
            self.db.commit()
        except Exception:  # noqa: BLE001 — reversal is best-effort; retried next poll
            self.db.rollback()
            return 0
        return restored

    def poll(self, *, dry_run: bool = False, max_orders: int | None = None) -> dict:
        """Process new Shopify orders since the watermark. Returns a summary dict.
        dry_run computes matches + would-decrement counts but writes nothing (no
        decrement, no processed marker, no watermark advance)."""
        if not self.is_configured():
            return {"ok": False, "error": "Shopify not configured."}

        wm = get_setting_value_db(self.db, "shopify_order_poll_watermark", "").strip()
        if not wm:
            # First run: look back a short window rather than the whole order history.
            wm = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        q = f"created_at:>='{wm}'"

        summary = {"ok": True, "dry_run": dry_run, "orders_seen": 0,
                   "orders_processed": 0, "orders_skipped": 0,
                   "orders_reversed": 0, "pieces_restored": 0,
                   "lines_matched": 0, "lines_unmatched": 0,
                   "pieces_decremented": 0, "errors": [], "watermark_in": wm}
        inv = InventoryService(self.db, self.current_user_id)
        cursor = None
        newest = wm            # newest createdAt handled cleanly
        error_floor = None     # oldest createdAt that errored — watermark won't pass it

        while True:
            data = self._shop._graphql(_ORDERS_QUERY, {"cursor": cursor, "q": q})
            if data.get("errors"):
                msg = str(data["errors"])[:300]
                summary["ok"] = False
                summary["error"] = msg
                low = msg.lower()
                if "access" in low or "scope" in low or "read_orders" in low:
                    summary["scope_error"] = (
                        "The Shopify token lacks read_orders — add that scope to the "
                        "custom app (Settings → Apps → Develop apps) and reinstall.")
                return summary
            block = (data.get("data") or {}).get("orders") or {}
            for o in block.get("nodes") or []:
                summary["orders_seen"] += 1
                oid = o.get("id") or ""
                created = o.get("createdAt") or ""
                exists = (self.db.query(ShopifyProcessedOrder)
                          .filter(ShopifyProcessedOrder.shopify_order_id == oid).first())
                if exists:
                    summary["orders_skipped"] += 1
                    # An order we already decremented that Shopify now reports
                    # cancelled must have its stock RESTORED (once) — otherwise a
                    # placed-then-cancelled sale permanently under-counts the shelf.
                    if (not dry_run and o.get("cancelledAt")
                            and exists.reversed_at is None
                            and exists.pieces_decremented):
                        restored = self._reverse_order(exists)
                        if restored:
                            summary["orders_reversed"] += 1
                            summary["pieces_restored"] += restored
                    if created and created > newest and error_floor is None:
                        newest = created
                    continue

                matched = unmatched = pieces = 0
                detail: list[dict] = []
                for li in (o.get("lineItems") or {}).get("nodes") or []:
                    qty = int(li.get("quantity") or 0)
                    sku = li.get("sku") or ""
                    vgid = (li.get("variant") or {}).get("id") or ""
                    cancelled = bool(o.get("cancelledAt"))
                    p = None if cancelled else self._match_product(sku, vgid)
                    if p is None:
                        unmatched += 1
                        summary["lines_unmatched"] += 1
                        detail.append({"sku": sku, "qty": qty, "matched": False,
                                       "cancelled": cancelled})
                        continue
                    pack = max(1, int(p.pack_qty or 1))
                    line_pieces = qty * pack
                    matched += 1
                    pieces += line_pieces
                    summary["lines_matched"] += 1
                    detail.append({"sku": sku, "product_id": p.id, "qty": qty,
                                   "pieces": line_pieces, "matched": True})
                    if not dry_run:
                        inv.record_shopify_sale(
                            p.id, line_pieces, order_ref_id=self._numeric(oid),
                            order_name=o.get("name") or "")
                summary["pieces_decremented"] += pieces

                if dry_run:
                    summary["orders_processed"] += 1
                    if created and created > newest and error_floor is None:
                        newest = created
                else:
                    self.db.add(ShopifyProcessedOrder(
                        shopify_order_id=oid, order_name=(o.get("name") or "")[:50],
                        order_created_at=(created or "")[:40], lines_matched=matched,
                        lines_unmatched=unmatched, pieces_decremented=pieces,
                        detail=json.dumps(detail)[:100000]))
                    try:
                        self.db.commit()
                        summary["orders_processed"] += 1
                        if created and created > newest and error_floor is None:
                            newest = created
                    except Exception as exc:  # noqa: BLE001 — retry this order next poll
                        self.db.rollback()
                        if len(summary["errors"]) < 10:
                            summary["errors"].append(
                                {"order": o.get("name"), "error": str(exc)[:200]})
                        # Don't let the watermark advance past a failed order.
                        if error_floor is None or (created and created < error_floor):
                            error_floor = created

                if max_orders and summary["orders_processed"] >= max_orders:
                    break

            page = block.get("pageInfo") or {}
            if (not page.get("hasNextPage")
                    or (max_orders and summary["orders_processed"] >= max_orders)):
                break
            cursor = page.get("endCursor")

        # Advance the watermark only up to the newest cleanly-handled order, and
        # never past an order that errored (so it is retried, not skipped).
        final = error_floor or newest
        summary["watermark_out"] = final
        if not dry_run and final and final != wm:
            set_setting_value_db(self.db, "shopify_order_poll_watermark", final)
            self.db.commit()
        return summary


def run_order_poll(user_id: int | None = None) -> None:
    """Background/scheduled order poll. Opens its OWN DB session and is fully
    fail-soft — records the outcome to settings for the UI, never raises."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        res = ShopifyOrderSyncService(db, user_id).poll()
        set_setting_value_db(db, "shopify_order_poll_last",
                             datetime.now().isoformat(timespec="seconds"))
        set_setting_value_db(
            db, "shopify_order_poll_last_summary",
            f"{res.get('orders_processed', 0)} orders, "
            f"{res.get('pieces_decremented', 0)} pieces, "
            f"{res.get('lines_unmatched', 0)} unmatched"
            + ("" if res.get("ok") else f" — ERROR: {res.get('error', '')[:120]}"))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    finally:
        db.close()
