"""Migration 043 – Create shipping_rates table for weight-based freight estimation."""
from __future__ import annotations


def migrate(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipping_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carrier TEXT NOT NULL,
            service TEXT NOT NULL DEFAULT 'Ground',
            min_weight_lbs REAL NOT NULL DEFAULT 0,
            max_weight_lbs REAL NOT NULL DEFAULT 999999,
            base_rate REAL NOT NULL DEFAULT 0,
            per_lb_rate REAL NOT NULL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Seed default rates for common carriers (heavy diesel parts)
    # These are conservative estimates; user can adjust in Settings
    defaults = [
        # UPS Ground – weight tiers
        ("UPS", "Ground", 0, 5, 12.00, 0.00),
        ("UPS", "Ground", 5, 20, 15.00, 0.50),
        ("UPS", "Ground", 20, 50, 20.00, 0.40),
        ("UPS", "Ground", 50, 70, 30.00, 0.35),
        ("UPS", "Ground", 70, 150, 45.00, 0.30),
        # FedEx Ground – weight tiers
        ("FedEx", "Ground", 0, 5, 11.50, 0.00),
        ("FedEx", "Ground", 5, 20, 14.50, 0.50),
        ("FedEx", "Ground", 20, 50, 19.00, 0.40),
        ("FedEx", "Ground", 50, 70, 28.00, 0.35),
        ("FedEx", "Ground", 70, 150, 42.00, 0.30),
        # Freight / LTL – heavier items
        ("Freight", "LTL", 0, 100, 75.00, 0.50),
        ("Freight", "LTL", 100, 250, 100.00, 0.40),
        ("Freight", "LTL", 250, 500, 150.00, 0.30),
        ("Freight", "LTL", 500, 1000, 200.00, 0.25),
        ("Freight", "LTL", 1000, 999999, 300.00, 0.20),
    ]

    existing = conn.execute("SELECT COUNT(*) FROM shipping_rates").fetchone()[0]
    if existing == 0:
        for carrier, service, min_w, max_w, base, per_lb in defaults:
            conn.execute(
                "INSERT INTO shipping_rates (carrier, service, min_weight_lbs, max_weight_lbs, base_rate, per_lb_rate) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (carrier, service, min_w, max_w, base, per_lb),
            )
    conn.commit()
