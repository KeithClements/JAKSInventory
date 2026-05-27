"""Core utilities for JAKS scraper."""

from core.stage import (
    STAGE_EMOJI,
    STAGE_TOOLTIP_ALL,
    get_stage,
    set_stage,
    promote_stage,
    coerce_stage_min1,
    looks_scraped,
    normalize_stage_on_load,
    stage_sort_key,
)
from core.paths import (
    OUTPUT_ROOT,
    SCOPED_SCRAPES_DIR,
    DIFF_EXPORTS_DIR,
    PERSISTENCE_DIR,
    slug_for_path,
    get_sku_folder,
    get_sku_images_folder,
)
from core.constants import (
    HHP_MAIN_CATEGORIES,
    MANUFACTURERS,
    REQUEST_TIMEOUT,
    DEFAULT_LOGO_PATH,
    ESTIMATES_FILE,
    LAST_SCRAPE_FILE,
    MIN_UI_MARGIN_WARN_PCT,
    MAX_UI_MARGIN_WARN_PCT,
    PAI_PORTAL_SESSION_DIR,
    PAI_PORTAL_LOGIN_URL,
    ENABLE_PAI_COST,
)

__all__ = [
    # stage
    "STAGE_EMOJI",
    "STAGE_TOOLTIP_ALL",
    "get_stage",
    "set_stage",
    "promote_stage",
    "coerce_stage_min1",
    "looks_scraped",
    "normalize_stage_on_load",
    "stage_sort_key",
    # paths
    "OUTPUT_ROOT",
    "SCOPED_SCRAPES_DIR",
    "DIFF_EXPORTS_DIR",
    "PERSISTENCE_DIR",
    "slug_for_path",
    "get_sku_folder",
    "get_sku_images_folder",
    # constants
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
