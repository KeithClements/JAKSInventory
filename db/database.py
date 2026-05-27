"""Database abstraction layer supporting SQLite and PostgreSQL.

Configuration via environment variables:
    DATABASE_URL=postgresql://user:pass@host:5432/dbname  # PostgreSQL
    DATABASE_URL=sqlite:///output/inventory.db            # SQLite (default)

For Supabase (free PostgreSQL hosting):
    DATABASE_URL=postgresql://postgres.[project]:[password]@aws-0-us-west-1.pooler.supabase.com:6543/postgres

Usage:
    from db.database import get_db, init_db
    
    db = get_db()
    db.execute("SELECT * FROM products WHERE sku = %s", (sku,))
    results = db.fetchall()
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Iterator
from pathlib import Path

# Load .env file to get DATABASE_URL
try:
    from dotenv_simple import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not available, rely on system environment

# Default to SQLite for local development
DEFAULT_DATABASE_URL = f"sqlite:///{Path(__file__).parent.parent / 'output' / 'JAKs.db'}"


def get_database_url() -> str:
    """Get database URL from environment or use default.

    Resolution order:
      1. DATABASE_URL  (full connection string, supports Postgres)
      2. JAKS_DB_PATH  (SQLite file path shortcut, e.g. for swapping
         between production and demo databases)
      3. Default: <repo>/output/inventory.db
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    db_path = os.environ.get("JAKS_DB_PATH")
    if db_path:
        return f"sqlite:///{db_path}"
    return DEFAULT_DATABASE_URL


def is_postgresql() -> bool:
    """Check if current database is PostgreSQL."""
    url = get_database_url()
    return url.startswith("postgres")


def parse_database_url(url: str) -> dict:
    """Parse database URL into components."""
    if url.startswith("sqlite"):
        match = re.match(r"sqlite:///(.+)", url)
        path = match.group(1) if match else "inventory.db"
        return {"backend": "sqlite", "path": path}
    
    elif url.startswith("postgres"):
        # postgresql://user:pass@host:port/dbname
        match = re.match(
            r"postgres(?:ql)?://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)",
            url
        )
        if match:
            return {
                "backend": "postgresql",
                "user": match.group(1),
                "password": match.group(2),
                "host": match.group(3),
                "port": int(match.group(4)),
                "database": match.group(5),
            }
        raise ValueError(f"Invalid PostgreSQL URL: {url}")
    
    raise ValueError(f"Unsupported database URL: {url}")


class DatabaseConnection(ABC):
    """Abstract database connection interface."""
    
    @abstractmethod
    def execute(self, query: str, params: tuple = ()) -> Any:
        """Execute a query."""
        pass
    
    @abstractmethod
    def executemany(self, query: str, params_list: list[tuple]) -> Any:
        """Execute a query with multiple parameter sets."""
        pass
    
    @abstractmethod
    def fetchone(self) -> dict | None:
        """Fetch one row as dict."""
        pass
    
    @abstractmethod
    def fetchall(self) -> list[dict]:
        """Fetch all rows as dicts."""
        pass
    
    @abstractmethod
    def commit(self) -> None:
        """Commit transaction."""
        pass
    
    @abstractmethod
    def close(self) -> None:
        """Close connection."""
        pass
    
    @property
    @abstractmethod
    def lastrowid(self) -> int | None:
        """Get last inserted row ID."""
        pass
    
    @abstractmethod
    def executescript(self, script: str) -> None:
        """Execute multiple SQL statements."""
        pass


class _DictRow(dict):
    """Dict subclass that also supports integer-index access like a tuple."""
    def __init__(self, row):
        super().__init__(row)
        self._values = list(self.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class SQLiteConnection(DatabaseConnection):
    """SQLite database connection."""
    
    def __init__(self, path: str):
        import sqlite3
        
        # Ensure directory exists
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._cursor = None
        
        # Run quick migrations for new columns
        self._apply_migrations()
    
    def _apply_migrations(self):
        """Apply any pending column additions."""
        try:
            # Check if uploaded_at column exists
            cols = [row[1] for row in self._conn.execute("PRAGMA table_info(products)").fetchall()]
            if "uploaded_at" not in cols:
                self._conn.execute("ALTER TABLE products ADD COLUMN uploaded_at TIMESTAMP")
                self._conn.commit()
        except Exception:
            pass  # Table might not exist yet

        # Product workbench columns — many legacy migrations add these but
        # may not have run on every dev DB. Adding them here keeps Edit /
        # Add Product safe and lets sections actually persist their values.
        try:
            cols = {row[1] for row in self._conn.execute(
                "PRAGMA table_info(products)").fetchall()}
            _PW_PRODUCT_COLS = [
                # Basic
                ("brief_description",          "TEXT"),
                ("oem_part_number",            "TEXT"),
                # Core charge
                ("has_core",                   "INTEGER DEFAULT 0"),
                ("core_sell_price",            "REAL DEFAULT 0"),
                ("core_return_days",           "INTEGER DEFAULT 30"),
                ("core_notes",                 "TEXT"),
                # Platform
                ("oem_manufacturer",           "TEXT"),
                ("truck_manufacturer",         "TEXT"),
                ("truck_model",                "TEXT"),
                ("truck_system",               "TEXT"),
                # Supplier extras
                ("vendor_description",         "TEXT"),
                ("vendor_availability",        "TEXT"),
                ("vendor_alternates",          "TEXT"),
                ("private_label",              "INTEGER DEFAULT 0"),
                ("private_label_part_number",  "TEXT"),
                # Warranty
                ("supplier_warranty_enabled",  "INTEGER DEFAULT 0"),
                ("supplier_warranty_value",    "INTEGER DEFAULT 0"),
                ("supplier_warranty_unit",     "TEXT DEFAULT 'days'"),
                ("jaks_warranty_enabled",      "INTEGER DEFAULT 0"),
                ("jaks_warranty_value",        "INTEGER DEFAULT 0"),
                ("jaks_warranty_unit",         "TEXT DEFAULT 'months'"),
                ("jaks_warranty_charge",       "REAL DEFAULT 0"),
                ("warranty_percentage",        "REAL DEFAULT 10.0"),
                ("is_warrantable",             "INTEGER DEFAULT 1"),
                # Inventory
                ("subcategory",                "TEXT"),
                ("bin_location",               "TEXT"),
                ("requires_serial",            "INTEGER DEFAULT 0"),
                ("requires_serial_receive",    "INTEGER DEFAULT 0"),
                ("requires_serial_warranty",   "INTEGER DEFAULT 0"),
                # Notes
                ("notes_public",               "TEXT"),
                ("notes_internal",             "TEXT"),
                # Shipping
                ("weight",                     "REAL DEFAULT 0"),
                ("weight_unit",                "TEXT DEFAULT 'LBS'"),
                ("length",                     "REAL DEFAULT 0"),
                ("width",                      "REAL DEFAULT 0"),
                ("height",                     "REAL DEFAULT 0"),
                ("dimension_unit",             "TEXT DEFAULT 'IN'"),
                ("shipping_cost",              "REAL DEFAULT 0"),
                # Shopify / SEO
                ("tags",                       "TEXT"),
                ("seo_title",                  "TEXT"),
                ("seo_description",            "TEXT"),
                ("publish_shopify",            "INTEGER DEFAULT 0"),
                # Pricing extras
                ("cost",                       "REAL DEFAULT 0"),
                ("price_category_id",          "INTEGER"),
                ("cost_updated_at",            "TIMESTAMP"),
                ("pricing_updated_at",         "TIMESTAMP"),
                # Misc fields legacy code reads
                ("xref_part_number",           "TEXT"),
                ("invoice_description",        "TEXT"),
                ("inventory_status",           "TEXT"),
            ]
            added = 0
            for name, sql_type in _PW_PRODUCT_COLS:
                if name in cols:
                    continue
                try:
                    self._conn.execute(
                        f"ALTER TABLE products ADD COLUMN {name} {sql_type}"
                    )
                    added += 1
                except Exception:
                    pass
            if added:
                self._conn.commit()
        except Exception:
            pass  # never block startup on these

        # Tier-pricing table for the workbench (Step 5b).
        try:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS product_qty_tiers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    min_qty INTEGER NOT NULL DEFAULT 1,
                    price REAL NOT NULL DEFAULT 0,
                    discount_pct REAL DEFAULT 0,
                    sort_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                        ON DELETE CASCADE
                )"""
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pqt_product "
                "ON product_qty_tiers (product_id, min_qty)"
            )
            self._conn.commit()
        except Exception:
            pass
    
    def execute(self, query: str, params: tuple = ()) -> "SQLiteConnection":
        # Convert %s placeholders to ? for SQLite
        query = query.replace("%s", "?")
        # Convert NOW() to CURRENT_TIMESTAMP for SQLite
        query = query.replace("NOW()", "CURRENT_TIMESTAMP")
        self._cursor = self._conn.execute(query, params)
        return self
    
    def executemany(self, query: str, params_list: list[tuple]) -> "SQLiteConnection":
        query = query.replace("%s", "?")
        self._cursor = self._conn.executemany(query, params_list)
        return self
    
    def fetchone(self) -> dict | None:
        if not self._cursor:
            return None
        row = self._cursor.fetchone()
        return _DictRow(row) if row else None
    
    def fetchall(self) -> list[dict]:
        if not self._cursor:
            return []
        return [_DictRow(row) for row in self._cursor.fetchall()]
    
    def commit(self) -> None:
        self._conn.commit()
    
    def rollback(self) -> None:
        self._conn.rollback()
    
    def close(self) -> None:
        self._conn.close()
    
    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid if self._cursor else None
    
    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount if self._cursor else 0
    
    def executescript(self, script: str) -> None:
        self._conn.executescript(script)
        self._conn.commit()


class PostgreSQLConnection(DatabaseConnection):
    """PostgreSQL database connection."""
    
    def __init__(self, user: str, password: str, host: str, port: int, database: str):
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError:
            raise ImportError(
                "psycopg2 not installed. Run: pip install psycopg2-binary"
            )
        
        self._conn = psycopg2.connect(
            user=user,
            password=password,
            host=host,
            port=port,
            database=database,
            sslmode="require",  # Required for Supabase
        )
        self._cursor = None
        self._lastrowid = None
    
    def execute(self, query: str, params: tuple = ()) -> "PostgreSQLConnection":
        import psycopg2.extras
        self._cursor = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Handle RETURNING for INSERT to get lastrowid
        if query.strip().upper().startswith("INSERT") and "RETURNING" not in query.upper():
            query = query.rstrip(";") + " RETURNING id"
        
        self._cursor.execute(query, params)
        
        # Capture lastrowid from RETURNING
        if query.strip().upper().startswith("INSERT"):
            try:
                result = self._cursor.fetchone()
                if result and "id" in result:
                    self._lastrowid = result["id"]
            except Exception:
                pass
        
        return self
    
    def executemany(self, query: str, params_list: list[tuple]) -> "PostgreSQLConnection":
        import psycopg2.extras
        self._cursor = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        psycopg2.extras.execute_batch(self._cursor, query, params_list)
        return self
    
    def fetchone(self) -> dict | None:
        if not self._cursor:
            return None
        try:
            row = self._cursor.fetchone()
            return dict(row) if row else None
        except Exception:
            return None
    
    def fetchall(self) -> list[dict]:
        if not self._cursor:
            return []
        try:
            return [dict(row) for row in self._cursor.fetchall()]
        except Exception:
            return []
    
    def commit(self) -> None:
        self._conn.commit()
    
    def close(self) -> None:
        if self._cursor:
            self._cursor.close()
        self._conn.close()
    
    @property
    def lastrowid(self) -> int | None:
        return self._lastrowid
    
    def executescript(self, script: str) -> None:
        """Execute multiple SQL statements for PostgreSQL."""
        import psycopg2.extras
        self._cursor = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Split on semicolons but handle edge cases
        statements = [s.strip() for s in script.split(";") if s.strip()]
        for stmt in statements:
            if stmt:
                try:
                    self._cursor.execute(stmt)
                except Exception as e:
                    # Skip "already exists" errors for CREATE TABLE IF NOT EXISTS
                    if "already exists" not in str(e).lower():
                        print(f"Warning executing statement: {e}")
        
        self._conn.commit()


def get_connection(url: str = None) -> DatabaseConnection:
    """Get a database connection based on URL.
    
    Falls back to SQLite if PostgreSQL is configured but psycopg2 isn't installed.
    """
    if url is None:
        url = get_database_url()
    
    config = parse_database_url(url)
    
    if config["backend"] == "sqlite":
        return SQLiteConnection(config["path"])
    elif config["backend"] == "postgresql":
        try:
            return PostgreSQLConnection(
                user=config["user"],
                password=config["password"],
                host=config["host"],
                port=config["port"],
                database=config["database"],
            )
        except ImportError:
            # psycopg2 not installed, fall back to SQLite
            import warnings
            warnings.warn("PostgreSQL configured but psycopg2 not installed, using SQLite fallback")
            sqlite_path = Path(__file__).parent.parent / "output" / "inventory.db"
            return SQLiteConnection(str(sqlite_path))
    else:
        raise ValueError(f"Unsupported backend: {config['backend']}")


@contextmanager
def get_db(url: str = None) -> Iterator[DatabaseConnection]:
    """Context manager for database connections."""
    conn = get_connection(url)
    try:
        yield conn
    finally:
        conn.close()


def get_backend() -> str:
    """Get current database backend name."""
    config = parse_database_url(get_database_url())
    return config["backend"]


# Convenience function for checking connection
def test_connection() -> bool:
    """Test database connection."""
    try:
        with get_db() as db:
            db.execute("SELECT 1")
            return True
    except Exception as e:
        print(f"Connection failed: {e}")
        return False
