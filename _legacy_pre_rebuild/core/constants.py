"""Application constants and configuration."""

from __future__ import annotations
import os
from pathlib import Path
import aiohttp

__all__ = [
    "HHP_MAIN_CATEGORIES",
    "MANUFACTURERS",
    "REQUEST_TIMEOUT",
    "DEFAULT_LOGO_PATH",
    "ESTIMATES_FILE",
    "LAST_SCRAPE_FILE",
    "MIN_UI_MARGIN_WARN_PCT",
    "MAX_UI_MARGIN_WARN_PCT",
    "PAI_PORTAL_SESSION_DIR",
    "PAI_PORTAL_LOGIN_URL",
    "ENABLE_PAI_COST",
]

# HTTP request timeout
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)

# Default watermark logo path
DEFAULT_LOGO_PATH = r"C:\Users\keith\OneDrive\Desktop\JAKs\Logo\Different Colors High Res\Green & Black.png"

# Data files
ESTIMATES_FILE = str(Path("data") / "estimates.json")
LAST_SCRAPE_FILE = Path("output") / "last_scrape.json"

# PAI portal configuration
PAI_PORTAL_SESSION_DIR = Path("pai_portal_session")
PAI_PORTAL_LOGIN_URL = "https://portal.pai.com/account/entrance?selected=accounts.profile"

# Kill switch (default off). Enable with env var: PAI_ENABLE=1
ENABLE_PAI_COST = str(os.getenv("PAI_ENABLE", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}

# UI-only warning threshold (do not block uploads; can be acknowledged)
MIN_UI_MARGIN_WARN_PCT = 30.0
MAX_UI_MARGIN_WARN_PCT = 90.0  # UX-008: Flag suspiciously high margins (>90%)

# HHP main product categories
HHP_MAIN_CATEGORIES = [
    "Engine Rebuild Kits",
    "Cylinder Head & Components",
    "Fuel Injectors",
    "Turbochargers",
    "Lubrication System",
    "Gaskets",
    "Piston Cylinder Kit & Components",
    "Fuel System",
    "Cooling System",
    "Exhaust System",
    "Crankshaft & Damper",
    "Air Intake System",
    "Miscellaneous",
]

# Supported manufacturers
MANUFACTURERS = [
    "Cummins",
    "Caterpillar",
    "Detroit Diesel",
    "Mack",
    "Volvo",
    "International",
]
