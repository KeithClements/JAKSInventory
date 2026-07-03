"""Pre-Inventory Service — staging area for parts before formal inventory.

Items land here from ESN Lookup, vendor searches, etc. They cannot be
sold or quoted until approved and promoted to real inventory.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from db.database import get_db
from db import create_product, update_product

logger = logging.getLogger(__name__)


class PreInventoryStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class PreInventoryItem:
    id: int = 0
    source: str = "manual"
    esn: str = ""
    manufacturer: str = ""
    oem_part_number: str = ""
    description: str = ""
    kit_number: str = ""
    kit_description: str = ""
    image_url: str = ""
    cost: float = 0.0
    selling_price: float = 0.0
    core_charge: float = 0.0
    pai_part_number: str = ""
    sampa_part_number: str = ""
    cross_refs: str = ""
    notes: str = ""
    grok_recommendation: str = ""
    status: str = "pending"
    created_at: str = ""
    reviewed_at: str = ""
    reviewed_by: str = ""
    product_id: Optional[int] = None


# ── CRUD ─────────────────────────────────────────────────────────────


def create_pre_inventory(data: dict) -> dict:
    """Insert a new pre-inventory staging record.

    Returns {"success": True, "id": <new_id>} or {"success": False, "error": ...}.
    """
    cols = ["source", "esn", "manufacturer", "oem_part_number", "description",
            "kit_number", "kit_description", "image_url", "cost",
            "selling_price", "core_charge", "pai_part_number",
            "sampa_part_number", "cross_refs", "notes", "grok_recommendation"]
    insert_cols = []
    insert_vals = []
    for c in cols:
        if c in data and data[c] is not None:
            insert_cols.append(c)
            insert_vals.append(data[c])

    if not insert_cols:
        return {"success": False, "error": "No data provided"}

    placeholders = ", ".join(["%s"] * len(insert_vals))
    col_str = ", ".join(insert_cols)

    try:
        with get_db() as conn:
            conn.execute(
                f"INSERT INTO pre_inventory ({col_str}) VALUES ({placeholders})",
                tuple(insert_vals),
            )
            conn.commit()
            new_id = conn.lastrowid
        return {"success": True, "id": new_id}
    except Exception as exc:
        logger.exception("create_pre_inventory failed")
        return {"success": False, "error": str(exc)}


def list_pre_inventory(status_filter: str | None = None) -> list[dict]:
    """Return pre-inventory items, optionally filtered by status."""
    with get_db() as conn:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM pre_inventory WHERE status = %s "
                "ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pre_inventory ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def get_pre_inventory_item(item_id: int) -> dict | None:
    """Get a single pre-inventory item by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM pre_inventory WHERE id = %s", (item_id,)
        ).fetchone()
    return dict(row) if row else None


def update_pre_inventory(item_id: int, **fields) -> bool:
    """Update fields on a pre-inventory record."""
    if not fields:
        return False
    set_parts = []
    values = []
    for key, value in fields.items():
        set_parts.append(f"{key} = %s")
        values.append(value)
    values.append(item_id)
    with get_db() as conn:
        conn.execute(
            f"UPDATE pre_inventory SET {', '.join(set_parts)} WHERE id = %s",
            tuple(values),
        )
        conn.commit()
        return conn.rowcount > 0


def delete_pre_inventory(item_id: int) -> bool:
    """Delete a pre-inventory item (only if pending)."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM pre_inventory WHERE id = %s AND status = 'pending'",
            (item_id,),
        )
        conn.commit()
        return conn.rowcount > 0


# ── Approve / Reject ─────────────────────────────────────────────────


def approve_item(item_id: int, reviewed_by: str = "system") -> dict:
    """Approve a pre-inventory item and promote it to real inventory.

    Creates a product via create_product(), sets inventory_status='active',
    and links the pre_inventory row via product_id.

    Returns {"success": True, "product_id": ...} or error dict.
    """
    item = get_pre_inventory_item(item_id)
    if not item:
        return {"success": False, "error": "Item not found"}
    if item["status"] != "pending":
        return {"success": False, "error": f"Item is already {item['status']}"}

    # Generate SKU
    from db.inventory import generate_sku
    sku = generate_sku(prefix="ESN" if item.get("esn") else "IMP")

    result = create_product(
        sku=sku,
        title=item.get("description") or item.get("kit_description") or "",
        oem_part_number=item.get("oem_part_number", ""),
        manufacturer=item.get("manufacturer", ""),
        cost=item.get("cost", 0),
        selling_price=item.get("selling_price", 0),
        core_charge=item.get("core_charge", 0),
        condition="New",
        engine=item.get("esn", ""),
    )
    if not result.get("success"):
        return result

    product_id = result["id"]

    # Mark active in products table
    update_product(product_id, inventory_status="active")

    # Add cross-reference interchanges if any
    _add_cross_refs(product_id, item)

    # Update pre_inventory record
    update_pre_inventory(
        item_id,
        status="approved",
        product_id=product_id,
        reviewed_at=datetime.now().isoformat(),
        reviewed_by=reviewed_by,
    )

    return {"success": True, "product_id": product_id, "sku": sku}


def reject_item(item_id: int, reason: str = "", reviewed_by: str = "system") -> bool:
    """Reject a pre-inventory item."""
    return update_pre_inventory(
        item_id,
        status="rejected",
        notes=reason,
        reviewed_at=datetime.now().isoformat(),
        reviewed_by=reviewed_by,
    )


def _add_cross_refs(product_id: int, item: dict):
    """Add PAI / SAMPA / cross-ref interchanges for the promoted product."""
    try:
        from db import add_interchange
        oem = item.get("oem_part_number", "")
        pai = item.get("pai_part_number", "")
        sampa = item.get("sampa_part_number", "")
        cross = item.get("cross_refs", "")

        parts = []
        if pai:
            parts.append(("PAI", pai))
        if sampa:
            parts.append(("SAMPA", sampa))
        if cross:
            for line in cross.split("\n"):
                line = line.strip()
                if line:
                    parts.append(("XREF", line))

        for source, pn in parts:
            try:
                add_interchange(product_id, pn, source)
            except Exception:
                pass
    except Exception:
        pass


# ── Enrichment helpers ───────────────────────────────────────────────


def enrich_pai(item_id: int) -> dict:
    """Run PAI enrichment on a pre-inventory item."""
    item = get_pre_inventory_item(item_id)
    if not item:
        return {"success": False, "error": "Not found"}
    oem = item.get("oem_part_number", "")
    if not oem:
        return {"success": False, "error": "No OEM part number to search"}

    try:
        from sources.pai_enrichment import enrich_pai_advisory
        pai_result = enrich_pai_advisory(oem)
        if pai_result:
            updates = {}
            if pai_result.get("pai_sku"):
                updates["pai_part_number"] = pai_result["pai_sku"]
            if pai_result.get("cost"):
                updates["cost"] = pai_result["cost"]
            if updates:
                update_pre_inventory(item_id, **updates)
            return {"success": True, "pai_result": pai_result}
    except Exception as exc:
        logger.exception("PAI enrichment failed")
        return {"success": False, "error": str(exc)}
    return {"success": False, "error": "No PAI result"}


def get_pre_inventory_count(status: str = "pending") -> int:
    """Get count of pre-inventory items by status."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM pre_inventory WHERE status = %s",
            (status,),
        ).fetchone()
    return row["cnt"] if row else 0
