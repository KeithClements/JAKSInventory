"""Manufacturer service for managing the manufacturers table.

Provides CRUD operations and lookup functions for manufacturers.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from db.database import get_connection


@dataclass
class Manufacturer:
    """Represents an actual manufacturer (not a reseller)."""
    id: int
    code: str                    # PAI, MCBEE, AFA, IPD, CUMMINS, CAT
    name: str                    # Full name: "PAI Industries"
    type: str                    # oem, aftermarket
    website: Optional[str] = None
    part_pattern: Optional[str] = None  # Regex for their part numbers
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def get_manufacturer_by_code(code: str) -> Optional[Manufacturer]:
    """Get a manufacturer by their code (PAI, MCBEE, etc.)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM manufacturers WHERE code = ?",
            (code.upper(),)
        ).fetchone()
        
        if row:
            return Manufacturer(
                id=row["id"],
                code=row["code"],
                name=row["name"],
                type=row["type"],
                website=row.get("website"),
                part_pattern=row.get("part_pattern"),
                is_active=bool(row.get("is_active", True)),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
            )
        return None
    finally:
        conn.close()


def get_manufacturer_by_id(mfr_id: int) -> Optional[Manufacturer]:
    """Get a manufacturer by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM manufacturers WHERE id = ?",
            (mfr_id,)
        ).fetchone()
        
        if row:
            return Manufacturer(
                id=row["id"],
                code=row["code"],
                name=row["name"],
                type=row["type"],
                website=row.get("website"),
                part_pattern=row.get("part_pattern"),
                is_active=bool(row.get("is_active", True)),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
            )
        return None
    finally:
        conn.close()


def get_all_manufacturers(type_filter: Optional[str] = None) -> list[Manufacturer]:
    """Get all manufacturers, optionally filtered by type (oem/aftermarket)."""
    conn = get_connection()
    try:
        if type_filter:
            rows = conn.execute(
                "SELECT * FROM manufacturers WHERE type = ? AND is_active = 1 ORDER BY name",
                (type_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM manufacturers WHERE is_active = 1 ORDER BY type, name"
            ).fetchall()
        
        return [
            Manufacturer(
                id=row["id"],
                code=row["code"],
                name=row["name"],
                type=row["type"],
                website=row.get("website"),
                part_pattern=row.get("part_pattern"),
                is_active=bool(row.get("is_active", True)),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
            )
            for row in rows
        ]
    finally:
        conn.close()


def get_oem_manufacturers() -> list[Manufacturer]:
    """Get all OEM manufacturers (Cummins, CAT, etc.)."""
    return get_all_manufacturers(type_filter="oem")


def get_aftermarket_manufacturers() -> list[Manufacturer]:
    """Get all aftermarket manufacturers (PAI, McBee, etc.)."""
    return get_all_manufacturers(type_filter="aftermarket")


def create_manufacturer(
    code: str,
    name: str,
    type: str,
    website: Optional[str] = None,
    part_pattern: Optional[str] = None,
) -> Manufacturer:
    """Create a new manufacturer."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO manufacturers (code, name, type, website, part_pattern)
               VALUES (?, ?, ?, ?, ?)""",
            (code.upper(), name, type, website, part_pattern)
        )
        conn.commit()
        
        # Fetch the created record
        return get_manufacturer_by_code(code)
    finally:
        conn.close()


def update_manufacturer(mfr: Manufacturer) -> bool:
    """Update an existing manufacturer."""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE manufacturers 
               SET name = ?, type = ?, website = ?, part_pattern = ?, 
                   is_active = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (mfr.name, mfr.type, mfr.website, mfr.part_pattern, 
             mfr.is_active, mfr.id)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def identify_manufacturer_from_part(part_number: str) -> Optional[Manufacturer]:
    """Identify manufacturer from part number using stored patterns.
    
    Falls back to engine.part_number_patterns if no DB match.
    """
    if not part_number:
        return None
    
    part_number = part_number.strip().upper()
    
    # First try the database patterns
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM manufacturers WHERE part_pattern IS NOT NULL AND is_active = 1"
        ).fetchall()
        
        import re
        for row in rows:
            pattern = row.get("part_pattern")
            if pattern:
                try:
                    if re.match(pattern, part_number, re.IGNORECASE):
                        return Manufacturer(
                            id=row["id"],
                            code=row["code"],
                            name=row["name"],
                            type=row["type"],
                            website=row.get("website"),
                            part_pattern=pattern,
                            is_active=True,
                        )
                except re.error:
                    continue
    finally:
        conn.close()
    
    # Fall back to pattern engine
    try:
        from engine.part_number_patterns import identify_manufacturer
        mfr_name = identify_manufacturer(part_number)
        if mfr_name:
            # Map the name to a database manufacturer
            code_map = {
                "PAI": "PAI",
                "Interstate McBee": "MCBEE",
                "AFA": "AFA",
                "IPD": "IPD",
                "OEM Cummins": "CUMMINS",
                "OEM CAT": "CAT",
            }
            code = code_map.get(mfr_name)
            if code:
                return get_manufacturer_by_code(code)
    except ImportError:
        pass
    
    return None
