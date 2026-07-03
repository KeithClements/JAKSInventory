from __future__ import annotations

import logging
import sys
from pathlib import Path

from collections import deque

from PySide6.QtCore import Qt, QEvent, QPoint, QTimer, Signal
from PySide6.QtGui import QFont, QShortcut, QKeySequence, QCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import init_db

log = logging.getLogger(__name__)

# Import theme from parent ui module
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ui.theme import get_theme_stylesheet

from .products_screen import ProductsScreen
from .vendors_screen import VendorsScreen
from .po_screen import POScreen, POReceiptsScreen
from .customers_screen import CustomersScreen
from .customers_hub_screen import CustomersHubScreen
from .invoices_screen import InvoicesScreen
from .reports_screen import ReportsScreen
from .barcode_screen import BarcodeScreen
from .settings_screen import SettingsScreen
from .locations_screen import LocationsScreen
from .cores_screen import CoresScreen
from .dashboard_screen import DashboardScreen
from .audit_screen import AuditScreen
from .sales_orders_screen import SalesOrdersScreen
from .returns_screen import ReturnsScreen
from .price_lists_screen import PriceListsScreen
from .vendor_returns_screen import VendorReturnsScreen  # deprecated, kept for deep-links
from .vendor_returns_combined_screen import VendorReturnsCombinedScreen
from .processing_center_screen import ProcessingCenterScreen
from .customer_cores_screen import CustomerCoresScreen
from .kits_screen import KitsScreen
from .margin_screen import MarginScreen
from .import_screen import ImportScreen
from .crm_screen import CRMScreen
from .aging_screen import AgingARScreen
from .qbo_screen import QBOScreen
from .sync_center import QBOSyncCenterScreen
from .low_stock_screen import LowStockScreen
from .quotes_screen import QuotesScreen
from .lost_sales_screen import LostSalesScreen
from .scraper_screen import ScraperScreen
from .hhp_scraper_screen import HHPScraperScreen
from .category_overview_screen import CategoryOverviewScreen
from .quick_nav_dialog import QuickNavDialog
from .pricing_overview_screen import PricingOverviewScreen
from .pricing_maintenance_screen import PricingMaintenanceScreen
from .tiered_pricing_screen import TieredPricingScreen
from .customer_list_screen import CustomerListScreen

from .customer_core_report_screen import CustomerCoreReportScreen
from .marketing_screen import MarketingScreen
from .messaging_screen import MessagingScreen
from .automation_screen import AutomationScreen
from .bulk_import_screen import BulkImportScreen
from .part_finder_screen import PartFinderScreen
from .adjustments_screen import AdjustmentsScreen
from .deliveries_screen import DeliveriesScreen
from .categories_brands_screen import CategoriesBrandsScreen

# Role for storing the stack index on tree items
_STACK_INDEX_ROLE = Qt.ItemDataRole.UserRole
_CAT_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
_CAT_CHILDREN_ROLE = Qt.ItemDataRole.UserRole + 2


class InventoryMainWindow(QMainWindow):
    # Cross-window data-change signals (Phase M). Other dialogs/screens can
    # subscribe to these to refresh themselves when something changes in
    # another window. Each signal carries the affected row id.
    customer_changed = Signal(int)
    product_changed = Signal(int)
    quote_changed = Signal(int)
    so_changed = Signal(int)
    invoice_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("JAKS Inventory")
        self.setMinimumSize(1000, 650)

        # Apply olive green theme
        self.setStyleSheet(get_theme_stylesheet())

        try:
            init_db()
            self._apply_migrations()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to initialize database:\n{e}")

        # Roll any past-due warranties to "expired" once per launch.
        try:
            from db import expire_due_warranties
            expire_due_warranties()
        except Exception:
            pass

        self._build_ui()

        if self._migration_errors:
            count = len(self._migration_errors)
            preview = "\n".join(f"  • {name}: {msg}" for name, msg in self._migration_errors[:5])
            more = "" if count <= 5 else f"\n  … and {count - 5} more"
            self.statusBar().showMessage(
                f"⚠ {count} migration warning(s) — see details on startup"
            )
            QMessageBox.warning(
                self,
                "Migration warnings",
                f"{count} migration file(s) reported errors (the app will still run):\n\n{preview}{more}",
            )
        else:
            self.statusBar().showMessage("Ready")
        self.setWindowModality(Qt.WindowModality.NonModal)

        # Tier 4: drain QBO webhook events in the background every 60s.
        # No-ops when the table is empty, so safe to run unconditionally.
        try:
            self._qbo_webhook_timer = QTimer(self)
            self._qbo_webhook_timer.setInterval(60_000)
            self._qbo_webhook_timer.timeout.connect(self._drain_qbo_webhook_events)
            self._qbo_webhook_timer.timeout.connect(self._refresh_qbo_status_pill)
            self._qbo_webhook_timer.start()
        except Exception:
            pass

        # A4: QBO health pill in the status bar.
        try:
            self._install_qbo_status_pill()
        except Exception as exc:
            log.debug("QBO status pill skipped: %s", exc)

        # Auto-refresh status pill (live "last refreshed Xs ago" indicator)
        try:
            self._install_refresh_status_pill()
        except Exception as exc:
            log.debug("refresh status pill skipped: %s", exc)

    # ------------------------------------------------------------------
    # A4: QBO status pill (always-visible health indicator)
    # ------------------------------------------------------------------
    def _install_refresh_status_pill(self) -> None:
        """Tiny green dot + 'auto-refresh' indicator on the status bar."""
        from datetime import datetime
        self._last_refresh_at = datetime.now()
        self._refresh_pill = QLabel("\u25CF  auto-refresh \u00b7 just now")
        self._refresh_pill.setStyleSheet(
            "QLabel { padding:2px 10px; border-radius:9px;"
            " background:#E8F5E9; color:#1B5E20; font-weight:600; }"
        )
        self._refresh_pill.setToolTip("Dashboard data auto-refreshes every minute.")
        try:
            self.statusBar().addPermanentWidget(self._refresh_pill)
        except Exception as exc:
            log.debug("status bar add refresh pill failed: %s", exc)
            return
        self._refresh_pill_timer = QTimer(self)
        self._refresh_pill_timer.setInterval(5_000)
        self._refresh_pill_timer.timeout.connect(self._update_refresh_pill)
        self._refresh_pill_timer.start()

    def _update_refresh_pill(self) -> None:
        """Update the 'last refreshed Xs ago' text."""
        from datetime import datetime
        pill = getattr(self, "_refresh_pill", None)
        last = getattr(self, "_last_refresh_at", None)
        if pill is None or last is None:
            return
        delta = (datetime.now() - last).total_seconds()
        if delta < 10:
            txt = "just now"
        elif delta < 60:
            txt = f"{int(delta)}s ago"
        elif delta < 3600:
            txt = f"{int(delta // 60)}m ago"
        else:
            txt = last.strftime("%I:%M %p")
        pill.setText(f"\u25CF  auto-refresh \u00b7 {txt}")

    def mark_data_refreshed(self) -> None:
        """Public hook screens can call after a successful data refresh."""
        from datetime import datetime
        self._last_refresh_at = datetime.now()
        self._update_refresh_pill()

    # ------------------------------------------------------------------
    # A4: QBO status pill (always-visible health indicator)
    # ------------------------------------------------------------------
    def _install_qbo_status_pill(self) -> None:
        """Put a clickable QBO health pill on the right side of the status bar.

        Shows: ``● <mode> · <N> pending · <N> failed``. Click navigates
        to the QBO Sync Center so the user can drill in.
        """
        self._qbo_pill = QPushButton("● QBO …")
        self._qbo_pill.setFlat(True)
        self._qbo_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._qbo_pill.setToolTip(
            "QBO sync status — click to open the QBO Sync Center."
        )
        self._qbo_pill.setStyleSheet(
            "QPushButton {"
            "  padding: 2px 10px; border-radius: 9px;"
            "  background: #ECEFF1; color: #263238;"
            "  font-weight: 600;"
            "}"
            "QPushButton:hover { background: #CFD8DC; }"
        )
        self._qbo_pill.clicked.connect(
            lambda: self._navigate_to_screen("QBO Sync Center")
        )
        try:
            self.statusBar().addPermanentWidget(self._qbo_pill)
        except Exception as exc:
            log.debug("status bar add pill failed: %s", exc)
            return
        self._refresh_qbo_status_pill()

    def _refresh_qbo_status_pill(self) -> None:
        """Recompute the pill label + color from current QBO state.

        Defensive — never raises, so a transient DB / QBO outage cannot
        crash the shell.
        """
        pill = getattr(self, "_qbo_pill", None)
        if pill is None:
            return
        # Mode
        try:
            from qbo.config import describe as _qbo_describe
            info = _qbo_describe() or {}
        except Exception:
            info = {}
        mode = (info.get("mode") or "unknown").replace("_", " ")
        mock = bool(info.get("mock"))

        # Pending webhook events
        pending = 0
        try:
            from qbo.webhook_worker import pending_count
            pending = int(pending_count() or 0)
        except Exception:
            pending = 0

        # Unresolved sync failures
        failed = 0
        try:
            from db.database import get_db
            with get_db() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM qbo_sync_log "
                    "WHERE status = 'failed' AND resolved_at IS NULL"
                ).fetchone()
                failed = int((row[0] if row else 0) or 0)
        except Exception:
            failed = 0

        parts = [f"● QBO {mode}"]
        if pending:
            parts.append(f"{pending} pending")
        if failed:
            parts.append(f"{failed} failed")
        pill.setText(" · ".join(parts))

        # Color: green=live read/write, yellow=mock, red=failures
        if failed:
            bg, fg = "#FFEBEE", "#B71C1C"     # red-ish
        elif mock or "mock" in mode:
            bg, fg = "#FFF8E1", "#F57F17"     # amber
        elif "write" in mode:
            bg, fg = "#E8F5E9", "#1B5E20"     # green
        else:
            bg, fg = "#E3F2FD", "#0D47A1"     # blue (read only)
        pill.setStyleSheet(
            "QPushButton {"
            "  padding: 2px 10px; border-radius: 9px;"
            f"  background: {bg}; color: {fg};"
            "  font-weight: 600;"
            "}"
            "QPushButton:hover { background: #CFD8DC; }"
        )

    def _drain_qbo_webhook_events(self) -> None:
        """Process pending QBO webhook events (best-effort, silent)."""
        try:
            from qbo.webhook_worker import pending_count, process_pending
            if pending_count() <= 0:
                return
            result = process_pending(limit=50)
            if result.get("failed"):
                log.warning("QBO webhook drain: %s", result)
        except Exception as exc:
            log.debug("QBO webhook drain skipped: %s", exc)

    # Populated by _apply_migrations so the window can surface failures.
    _migration_errors: list[tuple[str, str]] = []

    @classmethod
    def _apply_migrations(cls) -> None:
        """Run all numbered migration files against the current DB.

        Errors are collected (not silently swallowed) so the window can
        display a banner if any migration fails. Idempotent "already
        applied" errors are still collected but easy to spot in logs.

        Before running, take a timestamped backup of the SQLite database
        (skipped when running on Postgres).
        """
        import importlib
        import logging
        from pathlib import Path
        from db.database import get_db, get_database_url

        log = logging.getLogger(__name__)
        cls._migration_errors = []

        # Pre-migration backup (SQLite only)
        try:
            cls._backup_sqlite_db(get_database_url())
        except Exception as exc:  # pragma: no cover — backup is best-effort
            log.warning("Pre-migration backup skipped: %s", exc)

        migrations_dir = Path(__file__).resolve().parent.parent.parent / "db" / "migrations"
        if not migrations_dir.is_dir():
            return
        migration_files = sorted(migrations_dir.glob("[0-9]*.py"))
        with get_db() as conn:
            for mf in migration_files:
                mod_name = f"db.migrations.{mf.stem}"
                try:
                    mod = importlib.import_module(mod_name)
                    if hasattr(mod, "migrate"):
                        mod.migrate(conn)
                    elif hasattr(mod, "up"):
                        mod.up(conn)
                    elif hasattr(mod, "run"):
                        mod.run(conn)
                    elif hasattr(mod, "run_migration"):
                        mod.run_migration()
                except Exception as exc:
                    msg = f"{type(exc).__name__}: {exc}"
                    log.warning("Migration %s failed: %s", mf.stem, msg)
                    cls._migration_errors.append((mf.stem, msg))
            conn.commit()

    @staticmethod
    def _backup_sqlite_db(database_url: str, *, keep: int = 30) -> None:
        """Copy the SQLite DB to output/backups/<timestamp>.db before migrating.

        - No-op for non-SQLite URLs.
        - No-op when the DB file does not yet exist (fresh install).
        - Trims old backups beyond `keep` newest copies.
        """
        import shutil
        from datetime import datetime
        from pathlib import Path

        if not database_url.startswith("sqlite"):
            return
        # sqlite:///<path>
        db_path = database_url.split("sqlite:///", 1)[-1]
        src = Path(db_path)
        if not src.is_file() or src.stat().st_size == 0:
            return
        backup_dir = src.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = backup_dir / f"{src.stem}_{stamp}.db"
        shutil.copy2(src, dest)
        # Keep only the newest `keep` backups
        snaps = sorted(backup_dir.glob(f"{src.stem}_*.db"), reverse=True)
        for old in snaps[keep:]:
            try:
                old.unlink()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Sidebar structure: 6 categories. Tools screens are registered
    # separately (gear menu) and do not appear in the top nav.
    # ------------------------------------------------------------------
    _NAV_STRUCTURE = [
        ("DASHBOARD", None, [
            ("Dashboard", DashboardScreen),
        ]),
        ("SALES", None, [
            ("Quotes", QuotesScreen),
            ("Sales Orders", SalesOrdersScreen),
            ("Invoices", InvoicesScreen),
            ("Deliveries", DeliveriesScreen),
            ("Returns", ReturnsScreen),
            ("Cores", ProcessingCenterScreen),
            ("Lost Sales", LostSalesScreen),
            ("CRM", CRMScreen),
        ]),
        ("INVENTORY", None, [
            ("Products", ProductsScreen),
            ("Adjustments", AdjustmentsScreen),
            ("Warehouses & Bins", LocationsScreen),
            ("Kits", KitsScreen),
            ("Cycle Counts", AuditScreen),
            ("Categories & Brands", CategoriesBrandsScreen),
        ]),
        ("PURCHASING", None, [
            ("Purchase Orders", POScreen),
            ("PO Receipts", POReceiptsScreen),
            ("Vendors", VendorsScreen),
            ("Low Stock & Reorder", LowStockScreen),
            ("Vendor Returns", VendorReturnsCombinedScreen),
        ]),
        ("CUSTOMERS", None, [
            ("Customers", CustomersHubScreen),
            ("Text Messaging", MessagingScreen),
            ("SMS Campaigns", MarketingScreen),
            ("Automation", AutomationScreen),
        ]),
        ("ACCOUNTING", None, [
            ("QBO Sync Center", QBOSyncCenterScreen),
            ("QBO Reconciliation", QBOScreen),
            ("Aging AR", AgingARScreen),
            ("Margins", MarginScreen),
            ("Reports", ReportsScreen),
        ]),
    ]

    # Tools screens — instantiated and reachable by name (gear menu, Ctrl+K),
    # but intentionally not in the top nav bar.
    _TOOLS_SCREENS = [
        ("Part Finder",   PartFinderScreen),
        ("Barcodes",      BarcodeScreen),
        ("Customer Cores", CustomerCoresScreen),
        ("Pricing",       PricingOverviewScreen),
        ("Price Lists",   PriceListsScreen),
        ("Pricing Maintenance", PricingMaintenanceScreen),
        ("Tiered Pricing", TieredPricingScreen),
        ("Bulk Import",   BulkImportScreen),
        ("Import",        ImportScreen),
        ("HHP Scraper",   HHPScraperScreen),
        ("Scraper Admin", ScraperScreen),
    ]

    def _build_ui(self) -> None:
        """Build the main window with a horizontal top nav + persistent sub-tabs.

        Replaces the legacy left sidebar + hover flyout. The top bar holds
        category buttons (DASHBOARD, SALES, …) and a sub-tab strip below it
        shows the active category's child screens. No dropdowns.
        """
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Build stacked widget with all screens ──
        self._stack = QStackedWidget()
        stack_index = 0
        self._products_stack_index = 0
        self._screen_name_to_index: dict[str, int] = {}
        self._index_to_screen_name: dict[int, str] = {}
        # Category metadata for the top-nav shell.
        self._cat_children: dict[str, list[tuple[str, int]]] = {}
        self._cat_overview_index: dict[str, int] = {}
        self._cat_labels: list[tuple[str, str]] = []   # ordered (key, label)
        self._index_to_cat_key: dict[int, str] = {}
        # Recently visited screen names (most recent first), capped
        self._recent_screens: deque[str] = deque(maxlen=6)

        # ── Sub-tab bar (children of the active category) ──
        self._subtab_bar = QWidget()
        self._subtab_bar.setObjectName("subTabBar")
        self._subtab_bar.setFixedHeight(34)
        self._subtab_bar.setStyleSheet(
            """
            QWidget#subTabBar {
                background-color: #1a1f24;
                border-bottom: 1px solid #2f3942;
            }
            QToolButton#subTabBtn {
                color: #cfd5dc; background: transparent; border: none;
                padding: 0 16px; font-size: 9pt;
            }
            QToolButton#subTabBtn:hover { color: #ffffff; }
            QToolButton#subTabBtn[active="true"] {
                color: #ffffff; font-weight: 700;
                border-bottom: 2px solid #f0a850;
            }
            """
        )
        self._subtab_layout = QHBoxLayout(self._subtab_bar)
        self._subtab_layout.setContentsMargins(8, 0, 8, 0)
        self._subtab_layout.setSpacing(0)
        self._subtab_layout.addStretch(1)
        self._subtab_buttons: dict[int, QToolButton] = {}
        self._active_cat_key: str | None = None

        for label, screen_cls, children in self._NAV_STRUCTURE:
            if children is None:
                continue
            cat_key = label  # full label, e.g. "CORE PROCESSING"

            # Register child screens into stack
            child_entries: list[tuple[str, int]] = []
            first_child_index = stack_index
            for child_label, child_cls in children:
                if child_cls is ProductsScreen:
                    self._products_stack_index = stack_index
                self._screen_name_to_index[child_label.strip()] = stack_index
                screen = child_cls(parent=self)
                if hasattr(screen, "navigate_to"):
                    screen.navigate_to.connect(self._navigate_to_screen)
                if hasattr(screen, "navigate_to_settings"):
                    screen.navigate_to_settings.connect(
                        self._on_qbo_navigate_to_settings
                    )
                self._stack.addWidget(screen)
                child_entries.append((child_label, stack_index))
                self._index_to_screen_name[stack_index] = child_label.strip()
                self._index_to_cat_key[stack_index] = cat_key
                stack_index += 1

            # Category overview screen
            if cat_key == "DASHBOARD":
                overview_index = first_child_index
            elif cat_key == "PRICING":
                overview = PricingOverviewScreen(parent=self)
                if hasattr(overview, "navigate_to"):
                    overview.navigate_to.connect(self._navigate_to_screen)
                self._stack.addWidget(overview)
                overview_index = stack_index
                self._index_to_cat_key[stack_index] = cat_key
                stack_index += 1
            elif cat_key == "CUSTOMERS":
                overview = CustomersHubScreen(parent=self)
                if hasattr(overview, "navigate_to"):
                    overview.navigate_to.connect(self._navigate_to_screen)
                self._stack.addWidget(overview)
                overview_index = stack_index
                self._index_to_cat_key[stack_index] = cat_key
                stack_index += 1
            else:
                overview = CategoryOverviewScreen(cat_key, parent=self)
                overview.navigate_to.connect(self._navigate_to_screen)
                self._stack.addWidget(overview)
                overview_index = stack_index
                self._index_to_cat_key[stack_index] = cat_key
                stack_index += 1

            self._cat_children[cat_key] = child_entries
            self._cat_overview_index[cat_key] = overview_index
            self._cat_labels.append((cat_key, label))

        # Keep Settings as a dedicated screen (reached via gear / Ctrl+,).
        self._screen_name_to_index["Settings"] = stack_index
        settings_screen = SettingsScreen(parent=self)
        if hasattr(settings_screen, "navigate_to"):
            settings_screen.navigate_to.connect(self._navigate_to_screen)
        self._stack.addWidget(settings_screen)
        self._index_to_screen_name[stack_index] = "Settings"
        stack_index += 1

        # Register Tools screens — reachable via gear menu / Ctrl+K, but
        # not surfaced in the top nav bar.
        for tool_label, tool_cls in self._TOOLS_SCREENS:
            if tool_label in self._screen_name_to_index:
                continue  # already registered as a nav child
            self._screen_name_to_index[tool_label] = stack_index
            tool_screen = tool_cls(parent=self)
            if hasattr(tool_screen, "navigate_to"):
                tool_screen.navigate_to.connect(self._navigate_to_screen)
            self._stack.addWidget(tool_screen)
            self._index_to_screen_name[stack_index] = tool_label
            stack_index += 1

        # ── Top horizontal nav bar ──
        top_bar = QWidget()
        top_bar.setObjectName("topNavBar")
        top_bar.setFixedHeight(44)
        top_bar.setStyleSheet(
            """
            QWidget#topNavBar {
                background-color: #161b20;
                border-bottom: 1px solid #2f3942;
            }
            QToolButton#brandBtn {
                color: #f0a850; font-weight: 800; font-size: 12pt;
                background: transparent; border: none;
                padding: 0 18px 0 14px; letter-spacing: 0.5px;
            }
            QToolButton#catBtn {
                color: #cfd5dc; background: transparent; border: none;
                padding: 0 14px; font-weight: 700; font-size: 9pt;
                letter-spacing: 0.5px;
            }
            QToolButton#catBtn:hover { color: #ffffff; }
            QToolButton#catBtn[active="true"] {
                color: #f0a850;
                border-bottom: 3px solid #f0a850;
            }
            QLabel#topMeta {
                color: #8a939d; font-size: 9pt; padding: 0 14px;
                background: transparent;
            }
            QToolButton#gearBtn {
                color: #cfd5dc; background: transparent; border: none;
                padding: 0 14px; font-size: 14pt;
            }
            QToolButton#gearBtn:hover { color: #f0a850; }
            """
        )
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        brand_btn = QToolButton()
        brand_btn.setObjectName("brandBtn")
        brand_btn.setText("JAK'S DIESEL PRO")
        brand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        brand_btn.setAutoRaise(True)
        brand_btn.clicked.connect(
            lambda: self._navigate_to_category("DASHBOARD")
        )
        top_layout.addWidget(brand_btn)

        self._cat_buttons: dict[str, QToolButton] = {}
        for cat_key, display_label in self._cat_labels:
            btn = QToolButton()
            btn.setObjectName("catBtn")
            btn.setText(display_label)
            btn.setProperty("active", False)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAutoRaise(True)
            btn.clicked.connect(
                lambda checked=False, k=cat_key: self._navigate_to_category(k)
            )
            top_layout.addWidget(btn)
            self._cat_buttons[cat_key] = btn

        top_layout.addStretch(1)

        # Global search box (Ctrl+K) — opens Quick Nav with the typed query
        self._global_search = QLineEdit()
        self._global_search.setObjectName("globalSearch")
        self._global_search.setPlaceholderText("\U0001F50D  Search anything\u2026  (Ctrl+K)")
        self._global_search.setFixedWidth(260)
        self._global_search.setStyleSheet(
            "QLineEdit#globalSearch {"
            "  background:#1a2128; color:#e6edf3;"
            "  border:1px solid #2d3a45; border-radius:6px;"
            "  padding:4px 10px; font-size:11px;"
            "}"
            "QLineEdit#globalSearch:focus { border-color:#f0a850; }"
        )
        self._global_search.returnPressed.connect(self._on_global_search_submit)
        top_layout.addWidget(self._global_search)

        self._top_meta = QLabel("")
        self._top_meta.setObjectName("topMeta")
        top_layout.addWidget(self._top_meta)
        self._refresh_top_meta()
        self._top_meta_timer = QTimer(self)
        self._top_meta_timer.setInterval(30_000)
        self._top_meta_timer.timeout.connect(self._refresh_top_meta)
        self._top_meta_timer.start()

        # Gear button: opens a popup menu with Settings / Tools / Help.
        self._top_gear_btn = QToolButton()
        self._top_gear_btn.setObjectName("gearBtn")
        self._top_gear_btn.setText("\u2699")
        self._top_gear_btn.setToolTip("Settings, Tools, Help")
        self._top_gear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._top_gear_btn.setAutoRaise(True)
        self._top_gear_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        gear_menu = QMenu(self)
        gear_menu.setStyleSheet(
            "QMenu { background:#1a2128; color:#e6edf3; border:1px solid #2d3a45; padding:4px; }"
            "QMenu::item { padding:6px 18px; }"
            "QMenu::item:selected { background:#2d3a45; color:#f0a850; }"
            "QMenu::separator { height:1px; background:#2d3a45; margin:4px 8px; }"
        )
        gear_menu.addAction(
            "\u2699  Settings\tCtrl+,",
            lambda: self._navigate_to_screen("Settings"),
        )
        tools_sub = gear_menu.addMenu("\U0001F527  Tools")
        for tool_label, _cls in self._TOOLS_SCREENS:
            tools_sub.addAction(
                tool_label,
                lambda name=tool_label: self._navigate_to_screen(name),
            )
        gear_menu.addSeparator()
        gear_menu.addAction(
            "\u26A1  Quick Nav\tF1",
            self._on_quick_nav,
        )
        gear_menu.addAction(
            "\u2753  Keyboard Shortcuts",
            self._on_show_shortcuts,
        )
        self._top_gear_btn.setMenu(gear_menu)
        top_layout.addWidget(self._top_gear_btn)

        # Hidden placeholder kept for legacy callers that touch _top_title.
        self._top_title = QLabel("")
        self._top_title.hide()

        root_layout.addWidget(top_bar)
        root_layout.addWidget(self._subtab_bar)
        root_layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        # Ctrl+, shortcut to open Settings
        settings_sc = QShortcut(
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_Comma), self
        )
        settings_sc.activated.connect(
            lambda: self._navigate_to_screen("Settings")
        )

        # F1 global shortcut for Quick Nav
        f1 = QShortcut(QKeySequence(Qt.Key.Key_F1), self)
        f1.activated.connect(self._on_quick_nav)

        # Ctrl+K — focus global search box
        ctrl_k = QShortcut(
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_K), self
        )
        ctrl_k.activated.connect(self._focus_global_search)

        # F2 global shortcut for New Quote
        f2 = QShortcut(QKeySequence(Qt.Key.Key_F2), self)
        f2.activated.connect(self._on_quick_quote)

        # F3 global shortcut for Find Customer
        f3 = QShortcut(QKeySequence(Qt.Key.Key_F3), self)
        f3.activated.connect(self._on_quick_customer)

        # Global shortcuts that dispatch to the active screen when supported.
        # Each handler calls the first method on the active widget that exists
        # (from a list of candidate names), so screens opt-in by simply
        # implementing e.g. `on_shortcut_new` / `on_shortcut_find`.
        for seq, handler in (
            (QKeySequence.StandardKey.New, self._shortcut_new),
            (QKeySequence.StandardKey.Save, self._shortcut_save),
            (QKeySequence.StandardKey.Print, self._shortcut_print),
            (QKeySequence.StandardKey.Find, self._shortcut_find),
        ):
            sc = QShortcut(seq, self)
            sc.activated.connect(handler)

        self._go_to_index(self._products_stack_index)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------
    def _go_to_index(self, index: int) -> None:
        """Switch to a screen by stack index and reflect active state in nav."""
        self._stack.setCurrentIndex(index)
        cat_key = self._index_to_cat_key.get(index)
        if cat_key and cat_key != self._active_cat_key:
            self._set_active_category(cat_key)
        self._highlight_subtab(index)
        # Track recent screens (skip overview screens that have no display name).
        name = self._index_to_screen_name.get(index)
        if name:
            try:
                self._recent_screens.remove(name)
            except ValueError:
                pass
            self._recent_screens.appendleft(name)
            try:
                self.statusBar().showMessage(f"Viewing: {name}")
            except Exception:
                pass

    def _navigate_to_category(self, cat_key: str) -> None:
        """Open a category's overview screen (clicked from the top nav)."""
        idx = self._cat_overview_index.get(cat_key)
        if idx is None:
            return
        self._go_to_index(idx)

    def _set_active_category(self, cat_key: str) -> None:
        """Highlight the active top-nav category and rebuild its sub-tabs."""
        self._active_cat_key = cat_key
        for k, btn in self._cat_buttons.items():
            btn.setProperty("active", k == cat_key)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._rebuild_subtabs(cat_key)

    def _rebuild_subtabs(self, cat_key: str) -> None:
        """Recreate the sub-tab strip for the active category."""
        # Clear existing items (skip the trailing stretch we re-add below).
        while self._subtab_layout.count():
            item = self._subtab_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self._subtab_buttons.clear()

        children = self._cat_children.get(cat_key, [])

        # For multi-screen categories, include an "Overview" tab pointing
        # at the category landing page (unless the overview index is one
        # of the children, which is true only for DASHBOARD).
        overview_idx = self._cat_overview_index.get(cat_key)
        child_indices = [c[1] for c in children]
        # Suppress the auto-Overview sub-tab for categories whose first child
        # already serves as the landing page (mockup parity).
        _SKIP_OVERVIEW = {"INVENTORY"}
        if (
            overview_idx is not None
            and overview_idx not in child_indices
            and len(children) > 1
            and cat_key not in _SKIP_OVERVIEW
        ):
            ov_btn = QToolButton()
            ov_btn.setObjectName("subTabBtn")
            ov_btn.setText("Overview")
            ov_btn.setProperty("active", False)
            ov_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ov_btn.setAutoRaise(True)
            ov_btn.clicked.connect(
                lambda checked=False, i=overview_idx: self._go_to_index(i)
            )
            self._subtab_layout.addWidget(ov_btn)
            self._subtab_buttons[overview_idx] = ov_btn

        for child_label, child_index in children:
            btn = QToolButton()
            btn.setObjectName("subTabBtn")
            # Escape '&' so QToolButton doesn't treat it as a mnemonic accelerator
            btn.setText(child_label.replace("&", "&&"))
            btn.setProperty("active", False)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAutoRaise(True)
            btn.clicked.connect(
                lambda checked=False, i=child_index: self._go_to_index(i)
            )
            self._subtab_layout.addWidget(btn)
            self._subtab_buttons[child_index] = btn

        self._subtab_layout.addStretch(1)

    def _highlight_subtab(self, index: int) -> None:
        """Mark the sub-tab button for ``index`` as active."""
        for idx, btn in self._subtab_buttons.items():
            btn.setProperty("active", idx == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _refresh_top_meta(self) -> None:
        """Update the top-right date/time label."""
        from datetime import datetime
        try:
            self._top_meta.setText(datetime.now().strftime("%a, %b %d \u00b7 %I:%M %p"))
        except Exception:
            pass

    def _find_item_by_index(self, index: int):  # legacy stub
        return None

    def _on_open_settings(self) -> None:
        self._navigate_to_screen("Settings")

    def _select_screen_by_index(self, target_index: int) -> None:
        """Select the sidebar item matching the given stack index."""
        self._go_to_index(target_index)

    # ------------------------------------------------------------------
    # Global shortcut dispatch (Ctrl+N / Ctrl+S / Ctrl+P / Ctrl+F)
    # ------------------------------------------------------------------
    def _dispatch_shortcut(self, candidates: tuple[str, ...]) -> None:
        """Call the first matching method name on the current screen widget."""
        widget = self._stack.currentWidget()
        if widget is None:
            return
        for name in candidates:
            fn = getattr(widget, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    log.warning("shortcut %s failed: %s", name, exc)
                return

    def _shortcut_new(self) -> None:
        self._dispatch_shortcut((
            "on_shortcut_new", "_on_new", "_on_create", "_on_add",
        ))

    def _shortcut_save(self) -> None:
        self._dispatch_shortcut((
            "on_shortcut_save", "_on_save", "_save",
        ))

    def _shortcut_print(self) -> None:
        self._dispatch_shortcut((
            "on_shortcut_print", "_on_print", "_print",
        ))

    def _shortcut_find(self) -> None:
        widget = self._stack.currentWidget()
        if widget is None:
            return
        # Prefer explicit hook; otherwise focus a common search box.
        hook = getattr(widget, "on_shortcut_find", None)
        if callable(hook):
            hook()
            return
        for attr in ("_search", "_search_edit", "_search_box", "_filter_edit", "_search_input"):
            search_widget = getattr(widget, attr, None)
            if search_widget is not None and hasattr(search_widget, "setFocus"):
                search_widget.setFocus()
                if hasattr(search_widget, "selectAll"):
                    search_widget.selectAll()
                return

    # ------------------------------------------------------------------
    # Legacy sidebar / flyout handlers (kept as no-ops after the top-nav
    # refactor so any stray signal still fires safely).
    # ------------------------------------------------------------------
    def _on_nav_changed(self, current, _previous=None) -> None:
        return

    def _on_nav_activated(self, item, _column=0) -> None:
        return

    def _on_nav_item_hovered(self, item, _column=0) -> None:
        return

    def eventFilter(self, obj, event):
        return super().eventFilter(obj, event)

    def _show_flyout(self) -> None:
        return

    def _flyout_navigate(self, index: int) -> None:
        self._go_to_index(index)

    def _on_flyout_hidden(self) -> None:
        return

    def _dismiss_flyout(self) -> None:
        return

    def _dismiss_flyout_if_not_hovered(self) -> None:
        return

    # ------------------------------------------------------------------
    # Dashboard navigation support
    # ------------------------------------------------------------------
    def _navigate_to_screen(self, screen_name: str) -> None:
        """Navigate to a screen by its display name (called from dashboard cards)."""
        idx = self._screen_name_to_index.get(screen_name)
        if idx is not None:
            self._select_screen_by_index(idx)

    def _on_qbo_navigate_to_settings(self, op_key: str) -> None:
        """Handle Sync Center → Settings navigation requests.

        Sync Center's Manual Sync Actions buttons emit `navigate_to_settings(op_key)`
        because the operational implementations (and worker plumbing) currently
        live in `settings_screen._build_qbo_tab`. We switch to Settings → QBO
        tab, expand the (now collapsed-by-default) "Daily Operations" section,
        and trigger the matching button.
        """
        idx = self._screen_name_to_index.get("Settings")
        if idx is None:
            return
        self._select_screen_by_index(idx)
        settings = self._stack.widget(idx)
        if settings is None:
            return
        # Switch to QBO tab (currently index 5: Company, Invoice, Inventory,
        # Purchase Orders, Email/SMTP, QBO, System, Scraper).
        tabs = getattr(settings, "_tabs", None)
        if tabs is not None:
            for i in range(tabs.count()):
                if tabs.tabText(i).strip().upper().startswith("QBO"):
                    tabs.setCurrentIndex(i)
                    break
        # Map op_key → settings button attribute.
        button_map = {
            "sync_products": "_qbo_sync_products_btn",
            "sync_invoices": "_qbo_sync_invoices_btn",
            "sync_customers": "_qbo_sync_customers_btn",
            "sync_vendors": "_qbo_sync_vendors_btn",
            "push_inventory_qty": "_qbo_push_qty_btn",
            "import_customers": "_qbo_import_customers_btn",
            "import_vendors": "_qbo_import_vendors_btn",
            "import_products": "_qbo_import_products_btn",
        }
        attr = button_map.get(op_key)
        if attr and hasattr(settings, attr):
            btn = getattr(settings, attr)
            try:
                btn.click()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to trigger settings button %s", attr
                )
    # ------------------------------------------------------------------
    # Legacy sidebar search (no-op after the top-nav refactor).
    # ------------------------------------------------------------------
    def _on_sidebar_search(self, text: str) -> None:
        return

    def _on_sidebar_search_enter(self) -> None:
        return

    # ------------------------------------------------------------------
    # Global search
    # ------------------------------------------------------------------
    def _on_global_search(self) -> None:
        """Navigate to Products screen and trigger search with the entered text."""
        text = self._global_search.text().strip()
        if not text:
            return
        # Switch to products screen
        self._select_screen_by_index(self._products_stack_index)
        # Push search text into the products screen search box
        products_screen = self._stack.widget(self._products_stack_index)
        if hasattr(products_screen, "_search_edit"):
            products_screen._search_edit.setText(text)
            if hasattr(products_screen, "_on_search"):
                products_screen._on_search()

    # ------------------------------------------------------------------
    # Quick Quote
    # ------------------------------------------------------------------
    def _on_quick_nav(self) -> None:
        dlg = QuickNavDialog(parent=self)
        dlg.navigate_to.connect(self._navigate_to_screen)
        dlg.exec()

    # ------------------------------------------------------------------
    # Global search (Ctrl+K) — delegates to Quick Nav
    # ------------------------------------------------------------------
    def _focus_global_search(self) -> None:
        gs = getattr(self, "_global_search", None)
        if gs is None:
            return
        gs.setFocus()
        gs.selectAll()

    def _on_global_search_submit(self) -> None:
        gs = getattr(self, "_global_search", None)
        query = gs.text().strip() if gs is not None else ""
        # Open Quick Nav (it has its own filter). If the query exactly
        # matches a known screen name, jump straight there instead.
        if query and query in self._screen_name_to_index:
            self._navigate_to_screen(query)
            if gs is not None:
                gs.clear()
            return
        dlg = QuickNavDialog(parent=self)
        dlg.navigate_to.connect(self._navigate_to_screen)
        # Best-effort: pre-fill the dialog's search field if it exposes one.
        for attr in ("set_query", "_search", "search_edit", "_filter"):
            target = getattr(dlg, attr, None)
            if target is None:
                continue
            if callable(target):
                try:
                    target(query)
                except Exception:
                    pass
                break
            if hasattr(target, "setText"):
                try:
                    target.setText(query)
                except Exception:
                    pass
                break
        dlg.exec()
        if gs is not None:
            gs.clear()

    def _on_show_shortcuts(self) -> None:
        """Show a dialog summarising the global keyboard shortcuts."""
        QMessageBox.information(
            self,
            "Keyboard Shortcuts",
            "<b>Global shortcuts</b><br>"
            "Ctrl+K &nbsp; Search anything (focus search box)<br>"
            "F1 &nbsp;&nbsp;&nbsp;&nbsp; Quick Nav<br>"
            "F2 &nbsp;&nbsp;&nbsp;&nbsp; New Quote<br>"
            "F3 &nbsp;&nbsp;&nbsp;&nbsp; Find Customer<br>"
            "Ctrl+, &nbsp; Settings<br>"
            "Ctrl+N / Ctrl+S / Ctrl+P / Ctrl+F — dispatched to active screen",
        )

    def _on_quick_quote(self) -> None:
        """Open a new quote dialog from anywhere in the app."""
        from .quote_dialog import QuoteDialog
        open_new = True
        while open_new:
            open_new = False
            dlg = QuoteDialog(parent=self)
            dlg.exec()
            if dlg._save_and_new:
                open_new = True

    def _on_quick_customer(self) -> None:
        """F3: open Customer Quick-Pick → flows into Customer Profile."""
        from .customer_picker_dialog import CustomerPickerDialog
        from .customer_detail_dialog import CustomerDetailDialog

        picker = CustomerPickerDialog(parent=self)
        if picker.exec() != picker.DialogCode.Accepted:
            return
        if picker.create_new_requested:
            CustomerDetailDialog(customer_id=None, parent=self).exec()
            return
        cid = picker.selected_customer_id
        if cid:
            CustomerDetailDialog(customer_id=int(cid), parent=self).exec()