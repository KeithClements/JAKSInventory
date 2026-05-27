"""Shopify integration module for JAKS Inventory."""
from .orders import (
    fetch_shopify_orders,
    get_order_details,
    parse_order_for_inventory,
    process_shopify_order,
    sync_inventory_to_shopify,
    get_unfulfilled_orders,
    get_recent_orders,
)
from .collections import (
    create_smart_collection,
    get_smart_collections,
    get_smart_collection_by_handle,
    update_smart_collection,
    delete_smart_collection,
    create_custom_collection,
    get_custom_collections,
    add_product_to_collection,
    create_engine_collections,
    get_collection_products,
    ENGINE_COLLECTION_PRESETS,
)

__all__ = [
    # Orders
    "fetch_shopify_orders",
    "get_order_details",
    "parse_order_for_inventory",
    "process_shopify_order",
    "sync_inventory_to_shopify",
    "get_unfulfilled_orders",
    "get_recent_orders",
    # Collections
    "create_smart_collection",
    "get_smart_collections",
    "get_smart_collection_by_handle",
    "update_smart_collection",
    "delete_smart_collection",
    "create_custom_collection",
    "get_custom_collections",
    "add_product_to_collection",
    "create_engine_collections",
    "get_collection_products",
    "ENGINE_COLLECTION_PRESETS",
]
