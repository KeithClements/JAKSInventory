"""Migration 010: Daily route planner and customer geocoding.

Creates tables for:
- daily_routes: Daily route plans with status and metrics
- route_stops: Ordered stops within a route (visits, deliveries, pickups)
Adds columns:
- customers.latitude, customers.longitude for geocoding cache
"""

import logging

logger = logging.getLogger(__name__)


def _table_exists(conn, table_name: str) -> bool:
    try:
        cursor = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
            (table_name,))
        row = cursor.fetchone()
        if row:
            return bool(list(row.values())[0] if isinstance(row, dict) else row[0])
    except Exception:
        pass
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,))
        return cursor.fetchone() is not None
    except Exception:
        return False


def _get_existing_columns(conn, table_name: str) -> set:
    try:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        return {row[1] if isinstance(row, (tuple, list)) else row["name"] for row in cursor.fetchall()}
    except Exception:
        return set()


def _safe_add_column(conn, table_name: str, col_name: str, col_def: str):
    cols = _get_existing_columns(conn, table_name)
    if col_name not in cols:
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
            logger.info(f"Added column {col_name} to {table_name}")
        except Exception as e:
            if "duplicate" not in str(e).lower():
                logger.warning(f"Could not add column {col_name}: {e}")


def _safe_create_table(conn, sql: str, table_name: str):
    if not _table_exists(conn, table_name):
        conn.execute(sql)
        logger.info(f"Created table: {table_name}")


def up(conn) -> bool:
    """Run migration 010."""

    # Add lat/lng to customers for geocoding cache
    _safe_add_column(conn, "customers", "latitude", "REAL")
    _safe_add_column(conn, "customers", "longitude", "REAL")

    # Daily route plans
    _safe_create_table(conn, """
        CREATE TABLE daily_routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_date DATE NOT NULL UNIQUE,
            status TEXT DEFAULT 'planned',
            total_stops INTEGER DEFAULT 0,
            total_miles REAL DEFAULT 0,
            total_duration_min INTEGER DEFAULT 0,
            start_address TEXT DEFAULT 'Henderson, NV',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """, "daily_routes")

    # Route stops — each visit/delivery on the day
    _safe_create_table(conn, """
        CREATE TABLE route_stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id INTEGER NOT NULL REFERENCES daily_routes(id) ON DELETE CASCADE,
            stop_order INTEGER NOT NULL DEFAULT 0,
            customer_id INTEGER REFERENCES customers(id),
            stop_type TEXT NOT NULL DEFAULT 'visit',
            purpose TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,
            latitude REAL,
            longitude REAL,
            scheduled_time TEXT,
            actual_arrival TEXT,
            actual_departure TEXT,
            duration_min INTEGER DEFAULT 0,
            miles_from_prev REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            notes TEXT,
            shipment_id INTEGER REFERENCES shipments(id),
            activity_id INTEGER REFERENCES customer_activities(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """, "route_stops")

    # Indexes
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_daily_routes_date ON daily_routes(route_date)",
        "CREATE INDEX IF NOT EXISTS idx_daily_routes_status ON daily_routes(status)",
        "CREATE INDEX IF NOT EXISTS idx_route_stops_route ON route_stops(route_id)",
        "CREATE INDEX IF NOT EXISTS idx_route_stops_customer ON route_stops(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_route_stops_type ON route_stops(stop_type)",
        "CREATE INDEX IF NOT EXISTS idx_route_stops_shipment ON route_stops(shipment_id)",
        "CREATE INDEX IF NOT EXISTS idx_route_stops_activity ON route_stops(activity_id)",
        "CREATE INDEX IF NOT EXISTS idx_customers_latlon ON customers(latitude, longitude)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass

    conn.commit()
    logger.info("Migration 010 complete: daily routes + customer geocoding")
    return True
