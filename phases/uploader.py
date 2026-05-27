import json
import time
import base64
import requests
from datetime import datetime
from pathlib import Path
from utils.helpers import SHOPIFY_STORE, API_VERSION, ACCESS_TOKEN
from models import resolve_selling_price


def _normalize_sku(value: str) -> str:
    return str(value or "").strip().upper()


# ─────────────────────────────────────────────────────────────────────────────
# Shopify Sync Tracking (JSON file-based)
# ─────────────────────────────────────────────────────────────────────────────

SYNC_FILE = Path("output/_shopify_sync.json")


def _load_sync_data() -> dict:
    """Load sync tracking data from JSON file."""
    if not SYNC_FILE.exists():
        return {"products": {}, "last_full_sync": None}
    try:
        with open(SYNC_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"products": {}, "last_full_sync": None}


def _save_sync_data(data: dict) -> None:
    """Save sync tracking data to JSON file."""
    SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SYNC_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def _mark_shopify_synced(sku: str, shopify_product_id: int, product) -> None:
    """Mark a product as successfully synced to Shopify.
    
    This creates a local record so we know:
    - The product was uploaded to Shopify
    - The Shopify product ID (for updates)
    - When it was synced
    - Price at time of sync (to detect local changes)
    - Tags at time of sync (to detect local changes)
    """
    data = _load_sync_data()
    
    sku_key = _normalize_sku(sku)
    pricing = getattr(product, "pricing", {}) or {}
    price = float(pricing.get("selling_price", 0) or 0)
    title = str(getattr(product, "title", "") or "")
    tags = str(getattr(product, "tags", "") or "")
    
    data["products"][sku_key] = {
        "shopify_id": shopify_product_id,
        "synced_at": datetime.now().isoformat(),
        "price_synced": price,
        "tags_synced": tags,
        "status": "active",
        "title": title,
    }
    
    _save_sync_data(data)


def get_shopify_sync_status(sku: str) -> dict | None:
    """Get Shopify sync status for a SKU.
    
    Returns dict with shopify_id, synced_at, price_synced, status, title
    or None if not synced.
    """
    data = _load_sync_data()
    return data["products"].get(_normalize_sku(sku))


def is_synced_to_shopify(sku: str) -> bool:
    """Check if a product has been synced to Shopify."""
    return get_shopify_sync_status(sku) is not None


def get_all_synced_skus() -> set[str]:
    """Get set of all SKUs that have been synced to Shopify."""
    data = _load_sync_data()
    return set(data["products"].keys())


# ─────────────────────────────────────────────────────────────────────────────


def _iter_all_shopify_products(*, fields: str) -> list[dict]:
    """Fetch all products (paged) with given fields."""
    all_products: list[dict] = []
    if not ACCESS_TOKEN:
        return all_products

    endpoint: str | None = f"/products.json?limit=250&fields={fields}"
    while endpoint:
        resp = shopify_request("GET", endpoint)
        if not resp:
            break
        try:
            json_data = resp.json() or {}
        except Exception:
            json_data = {}
        all_products.extend(json_data.get("products", []) or [])

        link_header = resp.headers.get("Link", "")
        links = requests.utils.parse_header_links(link_header) if link_header else []
        endpoint = None
        for link in links:
            if link.get("rel") == "next" and link.get("url"):
                try:
                    endpoint = str(link["url"]).split("/admin/api", 1)[1]
                except Exception:
                    endpoint = None
                break

    return all_products


def fetch_shopify_sku_index() -> dict[str, list[dict]]:
    """Return map SKU -> list of {product_id, handle}."""
    index: dict[str, list[dict]] = {}
    if not ACCESS_TOKEN:
        return index

    products = _iter_all_shopify_products(fields="id,handle,variants")
    for p in products:
        handle = str(p.get("handle", "") or "").strip()
        pid = p.get("id")
        for v in (p.get("variants") or []):
            sku = _normalize_sku(v.get("sku", ""))
            if not sku:
                continue
            index.setdefault(sku, []).append({"product_id": pid, "handle": handle})
    return index


def fetch_all_shopify_products_for_sync(
    *, progress_callback: callable = None
) -> list[dict]:
    """
    Fetch ALL Shopify products with full details for sync comparison.
    Returns list of product dicts with: id, handle, title, status, variants, tags, template_suffix, updated_at
    """
    all_products: list[dict] = []
    if not ACCESS_TOKEN:
        return all_products

    fields = "id,handle,title,status,variants,tags,template_suffix,updated_at,images"
    endpoint: str | None = f"/products.json?limit=250&fields={fields}"
    page = 0
    
    while endpoint:
        page += 1
        if progress_callback:
            progress_callback(page, f"Fetching page {page}...")
        
        resp = shopify_request("GET", endpoint)
        if not resp:
            break
        try:
            json_data = resp.json() or {}
        except Exception:
            json_data = {}
        all_products.extend(json_data.get("products", []) or [])

        link_header = resp.headers.get("Link", "")
        links = requests.utils.parse_header_links(link_header) if link_header else []
        endpoint = None
        for link in links:
            if link.get("rel") == "next" and link.get("url"):
                try:
                    endpoint = str(link["url"]).split("/admin/api", 1)[1]
                except Exception:
                    endpoint = None
                break

    return all_products


def build_sync_status(
    local_products: dict,
    shopify_products: list[dict] = None,
    *,
    progress_callback: callable = None,
) -> dict:
    """
    Build comprehensive sync status comparing local products to Shopify.
    
    Returns:
        {
            'local_count': int,
            'shopify_count': int,
            'synced': [sku...],      # Exact match on Shopify
            'modified': [sku...],    # Different from Shopify version
            'not_uploaded': [sku...], # Not on Shopify
            'orphaned': [sku...],    # On Shopify but not in local (JAKS- prefix)
            'details': {sku: {...}}  # Detailed diff per product
        }
    """
    result = {
        'local_count': len(local_products or {}),
        'shopify_count': 0,
        'synced': [],
        'modified': [],
        'not_uploaded': [],
        'orphaned': [],
        'details': {},
    }
    
    if not ACCESS_TOKEN:
        result['error'] = 'No Shopify access token configured'
        result['not_uploaded'] = list((local_products or {}).keys())
        return result
    
    # Fetch Shopify products if not provided
    if shopify_products is None:
        if progress_callback:
            progress_callback(0, "Fetching Shopify products...")
        shopify_products = fetch_all_shopify_products_for_sync(progress_callback=progress_callback)
    
    result['shopify_count'] = len(shopify_products)
    
    # Build index by handle and SKU
    shopify_by_handle: dict[str, dict] = {}
    shopify_by_sku: dict[str, dict] = {}
    jaks_skus_on_shopify: set[str] = set()
    
    for sp in shopify_products:
        handle = str(sp.get("handle", "") or "").strip().lower()
        if handle:
            shopify_by_handle[handle] = sp
        
        for v in (sp.get("variants") or []):
            sku = _normalize_sku(v.get("sku", ""))
            if sku:
                shopify_by_sku[sku] = sp
                # Track JAKS products
                if sku.startswith("JAKS-"):
                    jaks_skus_on_shopify.add(sku)
    
    if progress_callback:
        progress_callback(50, "Comparing products...")
    
    # Compare each local product
    local_skus: set[str] = set()
    total = len(local_products or {})
    
    for idx, (key, p) in enumerate((local_products or {}).items()):
        sku = str(getattr(p, "sku", "") or "").strip().upper()
        handle = str(getattr(p, "handle", "") or "").strip().lower()
        local_skus.add(sku)
        
        if progress_callback and idx % 10 == 0:
            pct = 50 + int((idx / max(total, 1)) * 40)
            progress_callback(pct, f"Comparing {idx+1}/{total}...")
        
        # Try to find on Shopify by handle or SKU
        sp = shopify_by_handle.get(handle) or shopify_by_sku.get(sku)
        
        if not sp:
            result['not_uploaded'].append(sku)
            result['details'][sku] = {
                'status': 'not_uploaded',
                'local_title': str(getattr(p, "title", "") or ""),
                'local_handle': handle,
            }
            continue
        
        # Compare fields
        diffs = []
        sp_variant = ((sp.get("variants") or [{}])[0])
        
        # Price comparison - use selling_price if available, then price
        local_price = None
        if hasattr(p, "pricing") and isinstance(p.pricing, dict):
            local_price = p.pricing.get("selling_price") or p.pricing.get("price")
        
        sp_price = sp_variant.get("price")
        if local_price and sp_price:
            try:
                local_price_f = round(float(local_price), 2)
                sp_price_f = round(float(sp_price), 2)
                if local_price_f != sp_price_f:
                    diffs.append(f"price: ${sp_price_f:.2f} → ${local_price_f:.2f}")
            except (ValueError, TypeError):
                pass
        
        # Tags comparison
        local_tags = set(str(getattr(p, "tags", "") or "").split(",")) if hasattr(p, "tags") else set()
        local_tags = {t.strip() for t in local_tags if t.strip()}
        sp_tags = set(str(sp.get("tags", "") or "").split(","))
        sp_tags = {t.strip() for t in sp_tags if t.strip()}
        tags_added = local_tags - sp_tags
        tags_removed = sp_tags - local_tags
        if tags_added:
            diffs.append(f"+tags: {', '.join(sorted(tags_added)[:3])}{'...' if len(tags_added) > 3 else ''}")
        if tags_removed:
            diffs.append(f"-tags: {', '.join(sorted(tags_removed)[:3])}{'...' if len(tags_removed) > 3 else ''}")
        
        # Status
        sp_status = sp.get("status", "active")
        
        # Check if recently synced - if we have a sync record, trust it
        sync_status = get_shopify_sync_status(sku)
        if sync_status:
            # Product was uploaded through our system - check if local data changed since sync
            synced_price = sync_status.get("price_synced", 0)
            synced_tags = set(str(sync_status.get("tags_synced", "") or "").split(","))
            synced_tags = {t.strip() for t in synced_tags if t.strip()}
            
            local_price_current = None
            if hasattr(p, "pricing") and isinstance(p.pricing, dict):
                local_price_current = float(p.pricing.get("selling_price", 0) or 0)
            
            # Compare against what we synced, not what Shopify has
            price_unchanged = local_price_current and abs(local_price_current - synced_price) < 0.01
            tags_unchanged = local_tags == synced_tags
            
            # If nothing has changed locally since sync, consider it synced
            if price_unchanged and tags_unchanged:
                diffs = []  # Clear diffs - user hasn't changed anything since upload
        
        detail = {
            'status': 'modified' if diffs else 'synced',
            'shopify_id': sp.get("id"),
            'shopify_status': sp_status,
            'shopify_price': sp_price,
            'shopify_title': sp.get("title", ""),
            'shopify_updated': sp.get("updated_at", ""),
            'image_count': len(sp.get("images") or []),
            'diffs': diffs,
            'synced_at': sync_status.get("synced_at") if sync_status else None,
        }
        result['details'][sku] = detail
        
        if diffs:
            result['modified'].append(sku)
        else:
            result['synced'].append(sku)
    
    # Find orphaned products (JAKS- products on Shopify but not in local)
    orphaned = jaks_skus_on_shopify - local_skus
    result['orphaned'] = sorted(orphaned)
    
    # Add details for orphaned products
    for sku in orphaned:
        sp = shopify_by_sku.get(sku)
        if sp:
            sp_variant = ((sp.get("variants") or [{}])[0])
            result['details'][sku] = {
                'status': 'orphaned',
                'shopify_id': sp.get("id"),
                'shopify_title': sp.get("title", ""),
                'shopify_handle': sp.get("handle", ""),
                'shopify_status': sp.get("status", "active"),
                'shopify_price': sp_variant.get("price"),
                'shopify_updated': sp.get("updated_at", ""),
                'image_count': len(sp.get("images") or []),
                'shopify_tags': sp.get("tags", ""),
            }
    
    if progress_callback:
        progress_callback(100, "Sync complete")
    
    return result


def detect_sku_conflicts(products_dict, *, check_shopify: bool = True) -> list[dict]:
    """Detect SKU duplicates in-run and collisions with existing Shopify SKUs."""
    conflicts: list[dict] = []

    # 1) Duplicates within this run
    by_sku: dict[str, list[dict]] = {}
    for key, prod in (products_dict or {}).items():
        sku = _normalize_sku(getattr(prod, "sku", ""))
        if not sku:
            continue
        by_sku.setdefault(sku, []).append(
            {
                "key": str(key),
                "handle": str(getattr(prod, "handle", "") or ""),
                "title": str(getattr(prod, "title", "") or ""),
            }
        )
    for sku, items in by_sku.items():
        if len(items) > 1:
            conflicts.append({"type": "duplicate_run", "sku": sku, "items": items})

    # 2) Collisions with existing Shopify SKUs
    if check_shopify and ACCESS_TOKEN:
        sku_index = fetch_shopify_sku_index()
        for key, prod in (products_dict or {}).items():
            sku = _normalize_sku(getattr(prod, "sku", ""))
            if not sku:
                continue
            existing = sku_index.get(sku) or []
            if not existing:
                continue

            target_handle = str(getattr(prod, "handle", "") or "").strip().lower()
            mismatched = [
                e
                for e in existing
                if not target_handle or str(e.get("handle", "") or "").strip().lower() != target_handle
            ]
            if mismatched:
                conflicts.append(
                    {
                        "type": "collision_shopify",
                        "sku": sku,
                        "item": {
                            "key": str(key),
                            "handle": str(getattr(prod, "handle", "") or ""),
                            "title": str(getattr(prod, "title", "") or ""),
                        },
                        "existing": mismatched,
                    }
                )

    return conflicts

BASE_URL = f"https://{SHOPIFY_STORE}.myshopify.com/admin/api/{API_VERSION}"
headers = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

def shopify_request(method, endpoint, data=None):
    url = f"{BASE_URL}{endpoint}"

    def _throttle_from_headers(resp_headers) -> None:
        """Dynamic throttling based on Shopify call limit headers."""
        try:
            raw = resp_headers.get("X-Shopify-Shop-Api-Call-Limit", "")
            if not raw:
                return
            used_s, limit_s = (raw.split("/", 1) + [""])[:2]
            used = int(str(used_s).strip())
            limit = int(str(limit_s).strip())
            if limit <= 0:
                return
            remaining = limit - used

            # Simple, conservative backoff near the cap.
            # Shopify's REST bucket is small; when we're at 39/40 or 40/40, pause longer.
            if remaining <= 0:
                time.sleep(2.0)
            elif remaining <= 1:
                time.sleep(1.5)
            elif remaining <= 3:
                time.sleep(1.0)
            elif remaining <= 5:
                time.sleep(0.5)
        except Exception:
            return

    attempts = 0
    while True:
        attempts += 1
        try:
            r = requests.request(method, url, headers=headers, json=data)

            # Handle rate limit responses explicitly.
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    wait_s = float(retry_after) if retry_after is not None else 2.0
                except Exception:
                    wait_s = 2.0
                wait_s = max(0.5, min(wait_s, 10.0))
                print(f"Shopify rate limited (429). Sleeping {wait_s:.1f}s then retrying...")
                time.sleep(wait_s)
                if attempts < 6:
                    continue
                r.raise_for_status()

            r.raise_for_status()
            _throttle_from_headers(r.headers)
            return r  # Return full response object for access to headers and json
        except requests.RequestException as e:
            print(f"Shopify error {method} {endpoint}: {e}")
            return None

def get_or_create_collection(title):
    resp = shopify_request("GET", f"/custom_collections.json?title={title}")
    if resp:
        json_data = resp.json()
        if json_data.get("custom_collections"):
            return json_data["custom_collections"][0]["id"]
    payload = {"custom_collection": {"title": title}}
    result = shopify_request("POST", "/custom_collections.json", payload)
    if result:
        return result.json()["custom_collection"]["id"]
    return None

from engine.template_inference import infer_product_type, infer_template_suffix

def infer_tags(title: str, brand: str, engine: str, category: str, cpl_list=None, engine_model_tags=None, manufacturer: str = None) -> str:
    """Infer Shopify tags from product attributes.
    
    Args:
        title: Product title
        brand: OEM brand (e.g., Caterpillar)
        engine: Engine model (e.g., C15)
        category: Product category (e.g., Exhaust System)
        cpl_list: Optional list of CPL/ESN codes
        engine_model_tags: Optional list of OEM>Model tags
        manufacturer: Optional manufacturer for hierarchical tags (e.g., Volvo)
    
    Returns:
        Comma-separated tag string
    """
    t = title.lower()
    parts: list[str] = []
    for raw in (brand, engine, (category or "").lower()):
        val = str(raw or "").strip()
        if val:
            parts.append(val)
    base_tags = ",".join(parts)
    
    # Add hierarchical manufacturer > category tag for Shopify nested navigation
    # This creates tags like "Volvo>Exhaust System" for collection hierarchies
    if manufacturer and category:
        man_clean = str(manufacturer).strip()
        cat_clean = str(category).strip()
        if man_clean and cat_clean and man_clean.lower() not in {"all", "unknown"}:
            base_tags += f",{man_clean}>{cat_clean}"
    
    if "remanufactured" in t or "reman" in t:
        base_tags += ",remanufactured"
    elif "new" in t:
        base_tags += ",new"
    if "kit" in t and ("turbo" not in t and "turbocharger" not in t):
        base_tags += ",rebuild kit,inframe"
    if "injector" in t:
        base_tags += ",fuel injector"

    # Add CPL/ESN prefixes as tags
    if cpl_list:
        for raw in cpl_list:
            val = str(raw or "").strip()
            if not val:
                continue
            # Keep the original token (e.g., 2WS) but also add a prefixed tag.
            base_tags += f",{val}"
            if val.isdigit():
                base_tags += f",CPL-{val}"
            else:
                base_tags += f",ESN-{val}"

    # Add OEM & Engine model tags like "Caterpillar>C15"
    if engine_model_tags:
        for raw in engine_model_tags:
            val = str(raw or "").strip()
            if not val:
                continue
            # Tags are comma-separated; ensure no commas inside tag
            val = val.replace(",", " ")
            base_tags += f",{val}"
    # Add more based on title
    return base_tags


def _split_tags(tags: str) -> list[str]:
    raw = str(tags or "")
    parts = [t.strip() for t in raw.split(",")]
    return [t for t in parts if t]


def _fmt_money(value) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return ""


def _fetch_existing_product_by_handle(handle: str) -> dict | None:
    handle = str(handle or "").strip()
    if not handle:
        return None
    resp = shopify_request("GET", f"/products.json?handle={handle}")
    if not resp:
        return None
    try:
        data = resp.json() or {}
        products = data.get("products") or []
        return products[0] if products else None
    except Exception:
        return None


def build_desired_metafields(p) -> tuple[dict[str, str], set[str]]:
    """Return (desired_by_key, keys_we_manage) for namespace 'jaks'.
    
    Enhanced to include:
    - All cross-references (OEM, PAI, vendor, xrefs)
    - Vendor pricing comparison
    - Part number sources
    """
    title_l = str(getattr(p, "title", "") or "").lower()
    category_l = str(getattr(p, "category", "") or "").lower()

    is_probable_kit = any(k in title_l or k in category_l for k in ("rebuild kit", "inframe", "overhaul kit", "kit"))
    if "turbo" in title_l or "turbo" in category_l:
        is_probable_kit = False

    kit_contents = getattr(p, "kit_contents", []) or []
    kit_contents_value = "\n".join(f"• {i}" for i in kit_contents if str(i or "").strip()).strip()

    cpl_prefix_value = str(getattr(p, "cpl_prefix", "") or "").strip()
    cpl_list_value = ", ".join(getattr(p, "cpl_list", []) or []).strip()
    dimensions_value = str(getattr(p, "dimensions", "") or "").strip()
    weight_value = str(getattr(p, "weight", "") or "").strip()
    category_value = str(getattr(p, "category", "") or "").strip()
    
    # Build comprehensive cross-references list
    all_xrefs = []
    
    # OEM part number
    oem_pn = str(getattr(p, "oem_part_number", "") or "").strip()
    if oem_pn:
        all_xrefs.append(f"OEM: {oem_pn}")
        
    # PAI part number
    pai_sku = str(getattr(p, "pai_sku", "") or "").strip()
    if pai_sku:
        all_xrefs.append(f"PAI: {pai_sku}")
        
    # Supplier/vendor part number
    supplier_pn = str(getattr(p, "supplier_part_number", "") or "").strip()
    supplier = str(getattr(p, "supplier", "") or "").strip()
    if supplier_pn and supplier and supplier not in ("HHP", ""):
        all_xrefs.append(f"{supplier}: {supplier_pn}")
        
    # Other cross-references
    xrefs = getattr(p, "xrefs", []) or []
    for xref in xrefs:
        xref_str = str(xref or "").strip()
        if xref_str and xref_str not in all_xrefs:
            all_xrefs.append(xref_str)
            
    # PAI cross-reference (if adopted)
    pai_xref = str(getattr(p, "pai_cross_reference", "") or "").strip()
    if pai_xref and pai_xref not in all_xrefs and f"PAI: {pai_xref}" not in all_xrefs:
        all_xrefs.append(f"PAI XRef: {pai_xref}")
    
    xrefs_value = ", ".join(all_xrefs).strip()
    
    # Build vendor pricing display
    vendor_pricing_parts = []
    
    # PAI pricing
    pai_cost = getattr(p, "pai_cost", 0) or 0
    if pai_cost > 0:
        vendor_pricing_parts.append(f"PAI: ${pai_cost:.2f}")
        
    # Main cost (if different vendor)
    cost = getattr(p, "cost", 0) or 0
    if cost > 0 and supplier and supplier not in ("PAI", "HHP", ""):
        vendor_pricing_parts.append(f"{supplier}: ${cost:.2f}")
        
    # From vendor_pricing dict if available
    vendor_pricing_dict = getattr(p, "vendor_pricing", None)
    if vendor_pricing_dict and isinstance(vendor_pricing_dict, dict):
        for vendor, data in vendor_pricing_dict.items():
            if vendor not in ("PAI", supplier):
                v_cost = data.get("cost", 0)
                if v_cost > 0:
                    vendor_pricing_parts.append(f"{vendor}: ${v_cost:.2f}")
                    
    vendor_pricing_value = " | ".join(vendor_pricing_parts).strip()

    desired_by_key: dict[str, str] = {
        "category": category_value,
        "cross_references": xrefs_value,
        "cpl_prefix": cpl_prefix_value,
        "cpl_list": cpl_list_value,
        "dimensions": dimensions_value,
        "weight": weight_value,
    }
    
    # Add vendor pricing if we have it
    if vendor_pricing_value:
        desired_by_key["vendor_pricing"] = vendor_pricing_value
    
    if is_probable_kit and kit_contents_value:
        desired_by_key["kit_contents"] = kit_contents_value

    keys_we_manage = {
        "category",
        "cross_references",
        "cpl_prefix",
        "cpl_list",
        "kit_contents",
        "dimensions",
        "weight",
        "vendor_pricing",
    }
    return desired_by_key, keys_we_manage


def _fetch_existing_jaks_metafields(product_id: int) -> dict[str, dict]:
    """Return key -> {id, value} for namespace=jaks."""
    existing_by_key: dict[str, dict] = {}
    resp = shopify_request("GET", f"/products/{product_id}/metafields.json?namespace=jaks&limit=250")
    if not resp:
        return existing_by_key
    try:
        for mf in resp.json().get("metafields", []):
            k = mf.get("key")
            if not k:
                continue
            existing_by_key[str(k)] = {
                "id": mf.get("id"),
                "value": mf.get("value"),
            }
    except Exception:
        return {}
    return existing_by_key


def build_preupload_diffs(
    products_dict: dict,
    *,
    publish: bool,
    include_core: bool,
    markup_pct: float | None = None,
) -> list[dict]:
    """Compute Shopify pre-upload diffs for each product (price, cost, tags, jaks metafields).

    Returns list of dicts suitable for UI display.
    """
    diffs: list[dict] = []
    for key, p in (products_dict or {}).items():
        handle = str(getattr(p, "handle", "") or "").strip()
        sku = str(getattr(p, "sku", "") or "").strip()
        desired_payload = upload_product(None, p, publish, include_core, markup_pct=markup_pct)
        desired_product = (desired_payload or {}).get("product", {})
        desired_variant = ((desired_product.get("variants") or [{}])[0]) if desired_product else {}
        new_price = desired_variant.get("price")
        new_cost = desired_variant.get("cost")
        new_tags = set(_split_tags(desired_product.get("tags", "")))
        template_new = desired_product.get("template_suffix")

        existing_product = _fetch_existing_product_by_handle(handle)
        if not existing_product:
            diffs.append(
                {
                    "key": str(key),
                    "sku": sku,
                    "handle": handle,
                    "exists": False,
                    "price_old": None,
                    "price_new": _fmt_money(new_price),
                    "cost_old": None,
                    "cost_new": _fmt_money(new_cost),
                    "tags_added": sorted(new_tags),
                    "tags_removed": [],
                    "template_old": None,
                    "template_new": template_new,
                    "metafields_added": [],
                    "metafields_removed": [],
                    "metafields_changed": {},
                }
            )
            continue

        product_id = existing_product.get("id")
        old_tags = set(_split_tags(existing_product.get("tags", "")))
        old_variant = ((existing_product.get("variants") or [{}])[0])
        old_price = old_variant.get("price")
        old_cost = old_variant.get("cost")
        template_old = existing_product.get("template_suffix")

        desired_mf, keys_we_manage = build_desired_metafields(p)
        existing_mf = _fetch_existing_jaks_metafields(int(product_id)) if product_id else {}

        mf_added: list[str] = []
        mf_removed: list[str] = []
        mf_changed: dict[str, dict] = {}

        # removals: keys we manage that exist but are now blank/missing
        for k in keys_we_manage:
            desired_val = str(desired_mf.get(k, "") or "").strip()
            if not desired_val and k in existing_mf and str(existing_mf[k].get("value") or "").strip():
                mf_removed.append(k)

        for k, v in desired_mf.items():
            v_str = str(v or "")
            if not v_str.strip():
                continue
            if k not in existing_mf:
                mf_added.append(k)
            else:
                old_v = str(existing_mf[k].get("value") or "")
                if old_v != v_str:
                    mf_changed[k] = {"old": old_v, "new": v_str}

        diffs.append(
            {
                "key": str(key),
                "sku": sku,
                "handle": handle,
                "exists": True,
                "product_id": product_id,
                "price_old": _fmt_money(old_price),
                "price_new": _fmt_money(new_price),
                "cost_old": _fmt_money(old_cost),
                "cost_new": _fmt_money(new_cost),
                "tags_added": sorted(new_tags - old_tags),
                "tags_removed": sorted(old_tags - new_tags),
                "template_old": template_old,
                "template_new": template_new,
                "metafields_added": sorted(mf_added),
                "metafields_removed": sorted(mf_removed),
                "metafields_changed": mf_changed,
            }
        )

    return diffs

def upload_product(product_id, p, publish, include_core, *, markup_pct: float | None = None):
    pricing = getattr(p, "pricing", {}) or {}

    # Always resolve cost for the variant cost field.
    resolved_cost, _selling_ignored, _source = resolve_selling_price(
        p,
        markup_pct=0.0,
        include_core=False,
    )

    # PRIORITY: Use user-set selling_price if available
    # This allows manual price overrides to be respected
    user_selling_price = pricing.get("selling_price")
    if user_selling_price and float(user_selling_price) > 0:
        price = float(user_selling_price)
    elif markup_pct is None:
        # Back-compat: if no selling_price and no markup_pct, use scraped HHP price
        try:
            price = float(pricing.get("price", 0) or 0)
        except Exception:
            price = 0.0
    else:
        # Calculate price from cost + markup
        resolved_cost, price, _source = resolve_selling_price(
            p,
            markup_pct=float(markup_pct),
            include_core=bool(include_core),
        )

    # Note: resolve_selling_price already includes core when include_core=True.

    product_type = infer_product_type(p.title, getattr(p, "category", ""))
    template_suffix = infer_template_suffix(p.title, getattr(p, "category", ""), product_type)
    tags = infer_tags(
        p.title,
        p.brand,
        p.engine,
        p.category,
        getattr(p, "cpl_list", None),
        getattr(p, "engine_model_tags", None),
        manufacturer=getattr(p, "manufacturer", None),  # Pass manufacturer for hierarchical tags
    )
    product_payload = {
        "title": p.title,
        "body_html": p.description_jaks_html,
        "product_type": product_type,
        "tags": tags,
        "status": "active" if publish else "draft",
        "variants": [{
            "sku": p.sku,
            "price": str(round(float(price), 2)),
            "cost": str(round(float(resolved_cost), 2)),
        }]
    }

    # Only set Shopify Vendor on create; avoid overwriting existing vendor on updates.
    if not product_id:
        product_payload["vendor"] = "JAK's Diesel"
    if template_suffix:
        product_payload["template_suffix"] = template_suffix

    payload = {
        "product": {
            **product_payload
        }
    }
    return payload


def simulate_upload_plan(products_dict: dict) -> dict:
    """Dry-run: determine create vs update counts without writing to Shopify."""
    would_create = 0
    would_update = 0
    details: list[dict] = []

    for sku, p in (products_dict or {}).items():
        handle = str(getattr(p, "handle", "") or "").strip()
        existing_resp = shopify_request("GET", f"/products.json?handle={handle}")
        exists = False
        product_id = None
        if existing_resp:
            try:
                existing = existing_resp.json()
                if existing.get("products"):
                    exists = True
                    product_id = existing["products"][0].get("id")
            except Exception:
                exists = False

        if exists:
            would_update += 1
        else:
            would_create += 1

        details.append({"sku": str(sku), "handle": handle, "exists": exists, "product_id": product_id})

    return {"would_create": would_create, "would_update": would_update, "details": details}

def upload_images(product_id, images_dir, p):
    """Upload images to Shopify product.
    
    If product has images_selected, uses those specific paths.
    Otherwise searches for images in multiple possible locations:
    1. images_dir/handle/*.jpg (flat structure)
    2. images_dir/handle/images/*.jpg (nested images folder)
    3. images_dir/category/manufacturer/jaks_<handle>/images/*.jpg (full structure)
    """
    handle = getattr(p, "handle", "") or ""
    category = getattr(p, "category", "") or ""
    manufacturer = getattr(p, "manufacturer", "") or ""
    
    if not handle:
        print(f"   No handle for product, skipping images")
        return
    
    # Check if product has explicitly selected images
    images_selected = getattr(p, "images_selected", None) or []
    if images_selected:
        # Use user-selected images
        img_files = []
        for img_path_str in images_selected:
            img_path = Path(img_path_str)
            if img_path.exists():
                img_files.append(img_path)
            else:
                print(f"   ⚠ Selected image not found: {img_path}")
        
        if img_files:
            print(f"   Using {len(img_files)} user-selected images")
            for img_path in img_files:
                try:
                    with open(img_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    result = shopify_request("POST", f"/products/{product_id}/images.json",
                                    {"image": {"attachment": b64, "filename": img_path.name}})
                    if result:
                        print(f"   ✓ Uploaded {img_path.name}")
                except IOError as e:
                    print(f"   ✗ Image upload failed for {img_path}: {e}")
            return
        else:
            print(f"   ⚠ No selected images found, falling back to directory search")
    
    # Handle might have hyphens but folder uses underscores
    handle_underscore = handle.lower().replace("-", "_")
    
    # Try multiple possible paths
    possible_paths = [
        images_dir / handle,                                           # flat: images/handle
        images_dir / handle / "images",                                # nested: images/handle/images
        images_dir / f"jaks_{handle_underscore}" / "images",           # jaks prefix with underscores
        images_dir / f"jaks_{handle}" / "images",                      # jaks prefix original
    ]
    
    # Add category/manufacturer paths if available
    if category and manufacturer:
        cat_slug = category.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
        man_slug = manufacturer.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
        possible_paths.extend([
            images_dir / cat_slug / man_slug / f"jaks_{handle_underscore}" / "images",  # full with jaks_ prefix
            images_dir / cat_slug / man_slug / f"jaks_{handle}" / "images",
            images_dir / cat_slug / man_slug / handle / "images",      # full: category/mfr/handle/images
            images_dir / cat_slug / man_slug / handle,                 # full without images subdir
        ])
    
    # Also search in output root for category/mfr structure
    output_root = images_dir.parent if images_dir.name == "images" else images_dir
    if category and manufacturer:
        cat_slug = category.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
        man_slug = manufacturer.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
        possible_paths.extend([
            output_root / cat_slug / man_slug / f"jaks_{handle_underscore}" / "images",
            output_root / cat_slug / man_slug / f"jaks_{handle}" / "images",
            output_root / cat_slug / man_slug / handle / "images",
            output_root / cat_slug / man_slug / handle,
        ])
    
    # Find first path that exists and has images
    img_files = []
    found_path = None
    for path in possible_paths:
        if path.exists():
            jpg_files = list(path.glob("*.jpg"))
            if jpg_files:
                img_files = sorted(jpg_files)
                found_path = path
                break
    
    if not img_files:
        print(f"   No images found for {handle} (searched {len(possible_paths)} locations)")
        return
    
    print(f"   Found {len(img_files)} images in {found_path}")
    
    for img_path in img_files:
        try:
            with open(img_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            result = shopify_request("POST", f"/products/{product_id}/images.json",
                            {"image": {"attachment": b64, "filename": img_path.name}})
            if result:
                print(f"   ✓ Uploaded {img_path.name}")
        except IOError as e:
            print(f"   ✗ Image upload failed for {img_path}: {e}")

def add_metafields(product_id, p):
    desired_by_key, keys_we_manage = build_desired_metafields(p)

    # Fetch existing metafields in this namespace so we can update instead of endlessly POSTing.
    existing_by_key: dict[str, int] = {}
    existing = _fetch_existing_jaks_metafields(int(product_id))
    for k, info in (existing or {}).items():
        try:
            mid = info.get("id")
            if k and mid:
                existing_by_key[str(k)] = int(mid)
        except Exception:
            continue
    for key in keys_we_manage:
        desired_value = desired_by_key.get(key, "")
        mid = existing_by_key.get(key)
        if mid and not str(desired_value or "").strip():
            result = shopify_request("DELETE", f"/metafields/{mid}.json")
            if result:
                print(f"   Deleted metafield {key}")

    for key, value in desired_by_key.items():
        if not str(value or "").strip():
            continue

        mf = {"namespace": "jaks", "key": key, "value": value, "type": "string"}
        mid = existing_by_key.get(key)
        payload = {"metafield": mf}
        if mid:
            payload["metafield"]["id"] = mid
            result = shopify_request("PUT", f"/metafields/{mid}.json", payload)
            if result:
                print(f"   Updated metafield {key}: {str(value)[:50]}...")
        else:
            result = shopify_request("POST", f"/products/{product_id}/metafields.json", payload)
            if result:
                print(f"   Added metafield {key}: {str(value)[:50]}...")

def add_to_collections(product_id, p, collection_mapping):
    titles: list[str] = []

    # Category collection (e.g., Turbochargers)
    if getattr(p, "category", ""):
        titles.append(collection_mapping.get(p.category, p.category))

    # Manufacturer collection (e.g., Caterpillar)
    man = str(getattr(p, "manufacturer", "") or "").strip()
    if man and man.lower() not in {"all", "unknown"}:
        titles.append(collection_mapping.get(man, man))

    # Always include "Turbochargers" for turbo products (even if category naming is off)
    try:
        if infer_product_type(getattr(p, "title", "")) == "Turbocharger":
            titles.append("Turbochargers")
    except Exception:
        pass

    # De-dupe while preserving order
    titles = list(dict.fromkeys([t for t in titles if t]))

    for title in titles:
        collection_id = get_or_create_collection(title)
        if collection_id:
            shopify_request(
                "POST",
                "/collects.json",
                {"collect": {"product_id": product_id, "collection_id": collection_id}},
            )
            print(f"   Added to collection: {title}")

def upload_to_shopify(
    products_dict,
    images_dir,
    update_mode=True,
    publish=True,
    include_core=False,
    markup_pct: float | None = None,
    *,
    check_sku_conflicts: bool = True,
    allow_sku_conflicts: bool = False,
    progress_callback=None,
):
    # Engine-owned pricing enforcement (hard block): never mutate Shopify if pricing is invalid.
    from engine.product_engine import ProductEngine

    engine = ProductEngine()
    blocked: list[str] = []
    low_margin: list[str] = []
    low_abs_margin: list[str] = []
    for sku, p in (products_dict or {}).items():
        v = engine.validate_product(
            p,
            markup_pct=markup_pct,
            include_core=bool(include_core),
        )
        if v.is_blocked:
            blocked.append(str(getattr(p, "sku", "") or sku))
        if any(str(f).startswith("LOW_MARGIN") for f in (v.flags or [])):
            low_margin.append(str(getattr(p, "sku", "") or sku))
        if "LOW_ABSOLUTE_MARGIN" in (v.flags or []):
            low_abs_margin.append(str(getattr(p, "sku", "") or sku))

    if blocked:
        raise RuntimeError(
            "Pricing resolver blocked upload (missing/zero cost or selling price) for: " + ", ".join(blocked[:25])
        )

    if low_margin:
        print(f"WARN: {len(low_margin)} product(s) below margin threshold (engine): {', '.join(low_margin[:25])}")
    if low_abs_margin:
        print(
            f"WARN: {len(low_abs_margin)} product(s) below absolute margin threshold (engine): {', '.join(low_abs_margin[:25])}"
        )

    if check_sku_conflicts:
        conflicts = detect_sku_conflicts(products_dict, check_shopify=True)
        if conflicts:
            print(f"ERROR: {len(conflicts)} SKU conflict(s) detected")
            for c in conflicts[:25]:
                print(f" - {c.get('type')}: {c.get('sku')}")
            if not allow_sku_conflicts:
                # Build detailed error message with conflicting SKUs
                conflict_details = []
                for c in conflicts[:10]:  # Show up to 10 conflicts
                    ctype = c.get('type', 'UNKNOWN')
                    csku = c.get('sku', '')
                    existing = c.get('existing_handle', c.get('existing_sku', ''))
                    if existing:
                        conflict_details.append(f"  • {csku} ({ctype}) - conflicts with: {existing}")
                    else:
                        conflict_details.append(f"  • {csku} ({ctype})")
                details_str = "\n".join(conflict_details)
                if len(conflicts) > 10:
                    details_str += f"\n  ... and {len(conflicts) - 10} more"
                raise RuntimeError(
                    f"{len(conflicts)} SKU conflict(s) detected:\n\n{details_str}\n\n"
                    f"Fix: Edit the SKU in the main screen or Upload screen (right-click → Edit Product)"
                )

    collection_mapping = {
        "Turbochargers": "Turbochargers",
        "Fuel Injectors": "Fuel Injectors",
        "Lubrication System": "Oil Pumps & Coolers",
        "Gaskets": "Gaskets",
        "Cylinder Head & Components": "Cylinder Heads",
        "Piston Cylinder Kit & Components": "Pistons & Liners",
        "Detroit Diesel": "Detroit Diesel",
        "Exhaust System": "Exhaust Systems",
    }

    total_products = len(products_dict)
    for idx, (sku, p) in enumerate(products_dict.items(), 1):
        print(f"[{idx}/{total_products}] Uploading {sku}...")
        
        # Report progress
        if progress_callback:
            progress_callback(idx, total_products, sku)

        print("PHASE: Uploading to Shopify")
        existing_resp = shopify_request("GET", f"/products.json?handle={p.handle}")
        if existing_resp:
            existing = existing_resp.json()
            product_id = existing["products"][0]["id"] if existing.get("products") else None
        else:
            product_id = None

        payload = upload_product(product_id, p, publish, include_core, markup_pct=markup_pct)

        if product_id and update_mode:
            shopify_request("PUT", f"/products/{product_id}.json", payload)
            print("   Updated existing")
        else:
            result = shopify_request("POST", "/products.json", payload)
            if not result:
                continue
            product_id = result.json()["product"]["id"]
            print("   Created new")

        upload_images(product_id, images_dir, p)

        print("PHASE: Adding metafields")
        add_metafields(product_id, p)

        print("PHASE: Assigning collections")
        add_to_collections(product_id, p, collection_mapping)

        # Auto-sync to local database after successful Shopify upload
        print("PHASE: Syncing to database")
        try:
            from db import init_db, sync_product_to_db
            init_db()
            sync_product_to_db(p)
            
            # Mark product as synced to Shopify with ID and timestamp
            _mark_shopify_synced(sku, product_id, p)
            print("   Database synced")
        except Exception as db_err:
            print(f"   Database sync warning: {db_err}")

        print(f"   ✅ {sku} processed")


# NEW: Export full Shopify inventory to QBO CSV
def export_shopify_to_qbo_csv():
    """Fetch all products from Shopify and return QBO-ready CSV content"""
    all_products = []
    endpoint = "/products.json?limit=250"
    while endpoint:
        resp = shopify_request("GET", endpoint)
        if not resp:
            print("Error fetching products or empty response")
            break
        json_data = resp.json()
        all_products.extend(json_data["products"])

        # Pagination
        link_header = resp.headers.get("Link", "")
        links = requests.utils.parse_header_links(link_header)
        endpoint = None
        for link in links:
            if link.get("rel") == "next":
                endpoint = link["url"].split("/admin/api")[1]
                break

    if not all_products:
        print("No products found in Shopify")
        return None

    import csv
    from io import StringIO
    output = StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)  # Quote to escape commas in desc

    writer.writerow([
        "Name", "SKU", "Type", "Sales Price", "Cost", "Quantity On Hand",
        "Inventory Asset Account", "Sales Description", "Purchase Description"
    ])

    for product in all_products:
        for variant in product["variants"]:
            cost = variant.get("cost") or ""
            desc = product["body_html"][:255] if product["body_html"] else product["title"]
            writer.writerow([
                product["title"][:100],
                variant["sku"] or "",
                "Inventory",
                variant["price"],
                cost,
                variant["inventory_quantity"] or "0",
                "Inventory Asset",
                desc,
                desc
            ])

    output.seek(0)
    return output.getvalue()