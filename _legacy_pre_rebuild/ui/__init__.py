"""UI-layer helpers.

Keep UI entry/normalization steps here so all products entering the UI follow
one consistent path.
"""

from ui.theme import (
    THEME_COLORS,
    STATUS_BADGE_COLORS,
    BUTTON_STYLES,
    get_theme_stylesheet,
    get_status_badge_style,
    get_button_style,
)
from ui.widgets import (
    StatusBadge,
    PriceInput,
    StockIndicator,
    MarginDisplay,
)
from ui.product_entry import normalize_product_for_ui
from ui.dialogs import (
    confirm_dialog,
    error_dialog,
    info_dialog,
    warning_dialog,
    select_range_dialog,
    set_margin_dialog,
    adjust_cog_dialog,
    sku_conflict_dialog,
    preupload_diff_dialog,
    pai_alternate_dialog,
    ProductDetailDialog,
    WizardDialog,
)
from ui.table_manager import (
    ColumnConfig,
    COLUMN_CONFIGS,
    DEFAULT_HIDDEN_COLUMNS,
    TableManager,
)
from ui.workflow_stepper import (
    WorkflowPhase,
    WORKFLOW_PHASES,
    WorkflowStepper,
    PhaseButton,
    WorkflowStatusBar,
)
from ui.menus import (
    MenuManager,
    setup_keyboard_shortcuts,
    SHORTCUT_REFERENCE,
)
from ui.scope_selector import ScopeSelector
from ui.phase_panel import PhasePanel, PhaseButton as PhasePanelButton
from ui.upload_screen import UploadScreen

__all__ = [
    # Theme
    "THEME_COLORS",
    "STATUS_BADGE_COLORS",
    "BUTTON_STYLES",
    "get_theme_stylesheet",
    "get_status_badge_style",
    "get_button_style",
    # Widgets
    "StatusBadge",
    "PriceInput",
    "StockIndicator",
    "MarginDisplay",
    # Product entry
    "normalize_product_for_ui",
    # Dialogs
    "confirm_dialog",
    "error_dialog",
    "info_dialog",
    "warning_dialog",
    "select_range_dialog",
    "set_margin_dialog",
    "adjust_cog_dialog",
    "sku_conflict_dialog",
    "preupload_diff_dialog",
    "pai_alternate_dialog",
    "ProductDetailDialog",
    "WizardDialog",
    # Table manager
    "ColumnConfig",
    "COLUMN_CONFIGS",
    "DEFAULT_HIDDEN_COLUMNS",
    "TableManager",
    # Workflow stepper
    "WorkflowPhase",
    "WORKFLOW_PHASES",
    "WorkflowStepper",
    "PhaseButton",
    "WorkflowStatusBar",
    # Menus
    "MenuManager",
    "setup_keyboard_shortcuts",
    "SHORTCUT_REFERENCE",
    # Scope selector
    "ScopeSelector",
    # Phase panel
    "PhasePanel",
    "PhasePanelButton",
    # Upload screen
    "UploadScreen",
]
