"""Shopify SKU Cache Service.

Provides a fast in-memory cache of Shopify products for quick lookup
during the Phase 1-5 workflow. This allows the main table to show
which products already exist in Shopify without hitting the API.

Usage:
    from db.shopify_cache import shopify_cache
    
    # Load cache (call once at startup or after sync)
    shopify_cache.refresh()
    
    # Check if SKU exists in Shopify
    info = shopify_cache.get_sku_info("JAKS-12345")
    if info:
        print(f"Status: {info['status']}, ID: {info['shopify_id']}")
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class ShopifyCache:
    """Thread-safe cache of Shopify product SKUs and metadata."""
    
    def __init__(self):
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._last_refresh: datetime | None = None
        self._is_loading = False
    
    def refresh(self, progress_callback=None) -> int:
        """
        Refresh the cache from the database.
        
        Returns:
            Number of products cached
        """
        if self._is_loading:
            logger.debug("Cache refresh already in progress, skipping")
            return len(self._cache)
        
        self._is_loading = True
        try:
            from db.database import get_connection
            
            conn = get_connection()
            
            # Query all products that have been synced to Shopify
            rows = conn.execute("""
                SELECT sku, shopify_id, shopify_status, category, manufacturer, 
                       title, selling_price, shopify_synced_at
                FROM products 
                WHERE shopify_id IS NOT NULL
            """).fetchall()
            
            new_cache = {}
            for row in rows:
                # Handle both tuple and dict-like rows
                if isinstance(row, (list, tuple)):
                    sku = row[0]
                    info = {
                        "shopify_id": row[1],
                        "status": row[2] or "draft",
                        "category": row[3] or "",
                        "manufacturer": row[4] or "",
                        "title": row[5] or "",
                        "selling_price": row[6] or 0.0,
                        "synced_at": row[7] or "",
                    }
                else:
                    sku = row.get("sku") or row["sku"]
                    info = {
                        "shopify_id": row.get("shopify_id"),
                        "status": row.get("shopify_status") or "draft",
                        "category": row.get("category") or "",
                        "manufacturer": row.get("manufacturer") or "",
                        "title": row.get("title") or "",
                        "selling_price": row.get("selling_price") or 0.0,
                        "synced_at": row.get("shopify_synced_at") or "",
                    }
                
                if sku:
                    # Normalize SKU for lookup
                    normalized = self._normalize_sku(sku)
                    new_cache[normalized] = info
            
            with self._lock:
                self._cache = new_cache
                self._last_refresh = datetime.now()
            
            logger.info(f"Shopify cache refreshed: {len(new_cache)} products")
            return len(new_cache)
            
        except Exception as e:
            logger.warning(f"Failed to refresh Shopify cache: {e}")
            return 0
        finally:
            self._is_loading = False
    
    def _normalize_sku(self, sku: str) -> str:
        """Normalize SKU for consistent lookup."""
        if not sku:
            return ""
        return str(sku).strip().upper()
    
    def get_sku_info(self, sku: str) -> dict[str, Any] | None:
        """
        Get Shopify info for a SKU.
        
        Returns:
            Dict with shopify_id, status, category, manufacturer, etc.
            or None if not in Shopify
        """
        if not sku:
            return None
        
        normalized = self._normalize_sku(sku)
        with self._lock:
            return self._cache.get(normalized)
    
    def is_in_shopify(self, sku: str) -> bool:
        """Check if SKU exists in Shopify."""
        return self.get_sku_info(sku) is not None
    
    def get_status(self, sku: str) -> str | None:
        """Get Shopify status for SKU (active/draft/archived)."""
        info = self.get_sku_info(sku)
        return info.get("status") if info else None
    
    def get_shopify_id(self, sku: str) -> int | None:
        """Get Shopify product ID for SKU."""
        info = self.get_sku_info(sku)
        return info.get("shopify_id") if info else None
    
    def get_category(self, sku: str) -> str | None:
        """Get category from Shopify for SKU."""
        info = self.get_sku_info(sku)
        return info.get("category") if info else None
    
    def get_manufacturer(self, sku: str) -> str | None:
        """Get manufacturer from Shopify for SKU."""
        info = self.get_sku_info(sku)
        return info.get("manufacturer") if info else None
    
    @property
    def count(self) -> int:
        """Number of products in cache."""
        with self._lock:
            return len(self._cache)
    
    @property
    def last_refresh(self) -> datetime | None:
        """When cache was last refreshed."""
        return self._last_refresh
    
    @property
    def is_loaded(self) -> bool:
        """Whether cache has been loaded."""
        return self._last_refresh is not None
    
    def clear(self):
        """Clear the cache."""
        with self._lock:
            self._cache.clear()
            self._last_refresh = None


# Global singleton instance
shopify_cache = ShopifyCache()


def refresh_shopify_cache(progress_callback=None) -> int:
    """Convenience function to refresh the global cache."""
    return shopify_cache.refresh(progress_callback)


def get_shopify_info(sku: str) -> dict[str, Any] | None:
    """Convenience function to get Shopify info for a SKU."""
    return shopify_cache.get_sku_info(sku)


def is_sku_in_shopify(sku: str) -> bool:
    """Convenience function to check if SKU is in Shopify."""
    return shopify_cache.is_in_shopify(sku)
