"""Menu bar and toolbar setup for JAKS Scraper.

Centralizes menu actions, keyboard shortcuts, and toolbar configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMenuBar,
    QMenu,
    QToolBar,
    QWidget,
    QMainWindow,
)

if TYPE_CHECKING:
    pass

__all__ = [
    "MenuManager",
    "setup_keyboard_shortcuts",
]


class MenuManager:
    """Manages menu bar and toolbar setup."""

    def __init__(self, main_window: QMainWindow) -> None:
        self.window = main_window
        self.actions: dict[str, QAction] = {}
        self._menus: dict[str, QMenu] = {}

    def setup_menus(
        self,
        *,
        on_load_existing: Callable[[], None] | None = None,
        on_load_json: Callable[[], None] | None = None,
        on_save_json: Callable[[], None] | None = None,
        on_export_csv: Callable[[], None] | None = None,
        on_export_qbo: Callable[[], None] | None = None,
        on_exit: Callable[[], None] | None = None,
        on_select_all: Callable[[], None] | None = None,
        on_select_none: Callable[[], None] | None = None,
        on_select_ready: Callable[[], None] | None = None,
        on_select_by_stage: Callable[[int], None] | None = None,
        on_toggle_columns: Callable[[], None] | None = None,
        on_wizard: Callable[[], None] | None = None,
        on_about: Callable[[], None] | None = None,
    ) -> QMenuBar:
        """Set up the menu bar with all menus and actions."""
        menubar = self.window.menuBar()
        
        # File Menu
        file_menu = menubar.addMenu("&File")
        self._menus["file"] = file_menu
        
        if on_load_existing:
            action = QAction("Load &Existing", self.window)
            action.setShortcut(QKeySequence("Ctrl+O"))
            action.setStatusTip("Load existing products for current category/manufacturer")
            action.triggered.connect(on_load_existing)
            file_menu.addAction(action)
            self.actions["load_existing"] = action
        
        if on_load_json:
            action = QAction("Load Products &JSON...", self.window)
            action.setShortcut(QKeySequence("Ctrl+Shift+O"))
            action.setStatusTip("Load products from a JSON file")
            action.triggered.connect(on_load_json)
            file_menu.addAction(action)
            self.actions["load_json"] = action
        
        if on_save_json:
            action = QAction("&Save Products JSON...", self.window)
            action.setShortcut(QKeySequence("Ctrl+S"))
            action.setStatusTip("Save products to a JSON file")
            action.triggered.connect(on_save_json)
            file_menu.addAction(action)
            self.actions["save_json"] = action
        
        file_menu.addSeparator()
        
        if on_export_csv:
            action = QAction("Export to &CSV...", self.window)
            action.setShortcut(QKeySequence("Ctrl+E"))
            action.setStatusTip("Export products to CSV")
            action.triggered.connect(on_export_csv)
            file_menu.addAction(action)
            self.actions["export_csv"] = action
        
        if on_export_qbo:
            action = QAction("Export Shopify → &QBO CSV...", self.window)
            action.setStatusTip("Export Shopify inventory for QuickBooks")
            action.triggered.connect(on_export_qbo)
            file_menu.addAction(action)
            self.actions["export_qbo"] = action
        
        file_menu.addSeparator()
        
        if on_exit:
            action = QAction("E&xit", self.window)
            action.setShortcut(QKeySequence("Alt+F4"))
            action.triggered.connect(on_exit)
            file_menu.addAction(action)
            self.actions["exit"] = action
        
        # Edit Menu
        edit_menu = menubar.addMenu("&Edit")
        self._menus["edit"] = edit_menu
        
        if on_select_all:
            action = QAction("Select &All", self.window)
            action.setShortcut(QKeySequence("Ctrl+A"))
            action.triggered.connect(on_select_all)
            edit_menu.addAction(action)
            self.actions["select_all"] = action
        
        if on_select_none:
            action = QAction("Select &None", self.window)
            action.setShortcut(QKeySequence("Ctrl+Shift+A"))
            action.triggered.connect(on_select_none)
            edit_menu.addAction(action)
            self.actions["select_none"] = action
        
        if on_select_ready:
            action = QAction("Select &Ready", self.window)
            action.setShortcut(QKeySequence("Ctrl+R"))
            action.triggered.connect(on_select_ready)
            edit_menu.addAction(action)
            self.actions["select_ready"] = action
        
        if on_select_by_stage:
            edit_menu.addSeparator()
            
            stage_menu = edit_menu.addMenu("Select by &Stage")
            
            stages = [
                (1, "🔍 Stage 1 (Discovered)", "Ctrl+1"),
                (2, "🧱 Stage 2 (Scraped)", "Ctrl+2"),
                (3, "🧪 Stage 3 (PAI Enriched)", "Ctrl+3"),
                (4, "🧠 Stage 4 (Reviewed)", "Ctrl+4"),
                (5, "🚀 Stage 5 (Uploaded)", "Ctrl+5"),
            ]
            
            for stage, label, shortcut in stages:
                action = QAction(label, self.window)
                action.setShortcut(QKeySequence(shortcut))
                action.triggered.connect(lambda checked, s=stage: on_select_by_stage(s))
                stage_menu.addAction(action)
                self.actions[f"select_stage_{stage}"] = action
        
        # View Menu
        view_menu = menubar.addMenu("&View")
        self._menus["view"] = view_menu
        
        if on_toggle_columns:
            action = QAction("Show &Advanced Columns", self.window)
            action.setCheckable(True)
            action.setChecked(False)
            action.setShortcut(QKeySequence("Ctrl+Shift+C"))
            action.triggered.connect(on_toggle_columns)
            view_menu.addAction(action)
            self.actions["toggle_columns"] = action
        
        # Settings Menu (added for Phase 1 cleanup)
        settings_menu = menubar.addMenu("&Settings")
        self._menus["settings"] = settings_menu
        
        # Help Menu
        help_menu = menubar.addMenu("&Help")
        self._menus["help"] = help_menu
        
        if on_wizard:
            action = QAction("Open &Wizard...", self.window)
            action.setShortcut(QKeySequence("F1"))
            action.setStatusTip("Open the step-by-step workflow wizard")
            action.triggered.connect(on_wizard)
            help_menu.addAction(action)
            self.actions["wizard"] = action
        
        if on_about:
            action = QAction("&About JAKS Scraper", self.window)
            action.triggered.connect(on_about)
            help_menu.addAction(action)
            self.actions["about"] = action
        
        return menubar

    def setup_toolbar(
        self,
        *,
        on_load_existing: Callable[[], None] | None = None,
        on_save_json: Callable[[], None] | None = None,
        on_export_csv: Callable[[], None] | None = None,
        on_wizard: Callable[[], None] | None = None,
    ) -> QToolBar:
        """Set up the main toolbar."""
        toolbar = self.window.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        
        # Reuse actions from menu if available
        if "load_existing" in self.actions:
            toolbar.addAction(self.actions["load_existing"])
        
        if "save_json" in self.actions:
            toolbar.addAction(self.actions["save_json"])
        
        toolbar.addSeparator()
        
        if "export_csv" in self.actions:
            toolbar.addAction(self.actions["export_csv"])
        
        toolbar.addSeparator()
        
        if "wizard" in self.actions:
            toolbar.addAction(self.actions["wizard"])
        
        return toolbar

    def add_settings_action(
        self,
        name: str,
        label: str,
        callback: Callable[[], None],
        shortcut: str | None = None,
    ) -> QAction:
        """Add an action to the Settings menu."""
        if "settings" not in self._menus:
            return None
        action = QAction(label, self.window)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        self._menus["settings"].addAction(action)
        self.actions[name] = action
        return action

    def add_tools_action(
        self,
        name: str,
        label: str,
        callback: Callable[[], None],
        shortcut: str | None = None,
    ) -> QAction:
        """Add an action to the Tools menu (creates if needed)."""
        if "tools" not in self._menus:
            menubar = self.window.menuBar()
            # Insert before Help menu
            help_menu = self._menus.get("help")
            tools_menu = QMenu("&Tools", self.window)
            if help_menu:
                menubar.insertMenu(help_menu.menuAction(), tools_menu)
            else:
                menubar.addMenu(tools_menu)
            self._menus["tools"] = tools_menu
        
        action = QAction(label, self.window)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        self._menus["tools"].addAction(action)
        self.actions[name] = action
        return action

    def enable_action(self, name: str, enabled: bool = True) -> None:
        """Enable or disable an action by name."""
        if name in self.actions:
            self.actions[name].setEnabled(enabled)

    def set_action_checked(self, name: str, checked: bool) -> None:
        """Set checked state of a checkable action."""
        if name in self.actions and self.actions[name].isCheckable():
            self.actions[name].setChecked(checked)


def setup_keyboard_shortcuts(
    window: QMainWindow,
    *,
    on_phase_1: Callable[[], None] | None = None,
    on_phase_2: Callable[[], None] | None = None,
    on_phase_3: Callable[[], None] | None = None,
    on_phase_4: Callable[[], None] | None = None,
    on_phase_5: Callable[[], None] | None = None,
    on_stop: Callable[[], None] | None = None,
    on_refresh: Callable[[], None] | None = None,
    on_undo: Callable[[], None] | None = None,
    on_toggle_log: Callable[[], None] | None = None,
    on_show_family: Callable[[], None] | None = None,
    on_group_by_family: Callable[[], None] | None = None,
) -> list[QShortcut]:
    """
    Set up global keyboard shortcuts.
    
    Returns list of shortcuts (keep reference to prevent GC).
    """
    shortcuts = []
    
    # Phase shortcuts (Alt+1-5)
    if on_phase_1:
        sc = QShortcut(QKeySequence("Alt+1"), window)
        sc.activated.connect(on_phase_1)
        shortcuts.append(sc)
    
    if on_phase_2:
        sc = QShortcut(QKeySequence("Alt+2"), window)
        sc.activated.connect(on_phase_2)
        shortcuts.append(sc)
        # Also add Ctrl+Enter as primary scrape shortcut
        sc2 = QShortcut(QKeySequence("Ctrl+Return"), window)
        sc2.activated.connect(on_phase_2)
        shortcuts.append(sc2)
    
    if on_phase_3:
        sc = QShortcut(QKeySequence("Alt+3"), window)
        sc.activated.connect(on_phase_3)
        shortcuts.append(sc)
    
    if on_phase_4:
        sc = QShortcut(QKeySequence("Alt+4"), window)
        sc.activated.connect(on_phase_4)
        shortcuts.append(sc)
    
    if on_phase_5:
        sc = QShortcut(QKeySequence("Alt+5"), window)
        sc.activated.connect(on_phase_5)
        shortcuts.append(sc)
    
    # Stop (Escape)
    if on_stop:
        sc = QShortcut(QKeySequence("Escape"), window)
        sc.activated.connect(on_stop)
        shortcuts.append(sc)
    
    # Refresh (F5)
    if on_refresh:
        sc = QShortcut(QKeySequence("F5"), window)
        sc.activated.connect(on_refresh)
        shortcuts.append(sc)
    
    # Undo (Ctrl+Z)
    if on_undo:
        sc = QShortcut(QKeySequence("Ctrl+Z"), window)
        sc.activated.connect(on_undo)
        shortcuts.append(sc)
    
    # Toggle log (Ctrl+L)
    if on_toggle_log:
        sc = QShortcut(QKeySequence("Ctrl+L"), window)
        sc.activated.connect(on_toggle_log)
        shortcuts.append(sc)
    
    # Product Family shortcuts
    if on_show_family:
        sc = QShortcut(QKeySequence("F4"), window)
        sc.activated.connect(on_show_family)
        shortcuts.append(sc)
    
    if on_group_by_family:
        sc = QShortcut(QKeySequence("Ctrl+G"), window)
        sc.activated.connect(on_group_by_family)
        shortcuts.append(sc)
    
    return shortcuts


# Keyboard shortcut reference for help/wizard display
SHORTCUT_REFERENCE = """
Keyboard Shortcuts
==================

File Operations:
  Ctrl+O          Load existing products
  Ctrl+Shift+O    Load JSON file
  Ctrl+S          Save products JSON
  Ctrl+E          Export to CSV

Selection:
  Ctrl+A          Select all
  Ctrl+Shift+A    Select none
  Ctrl+R          Select ready
  Ctrl+1-5        Select by stage

Workflow Phases:
  Alt+1           Phase 1: Scan
  Alt+2           Phase 2: Scrape
  Ctrl+Enter      Scrape Category (primary)
  Alt+3           Phase 3: PAI Enrich
  Alt+4           Phase 4: Review
  Alt+5           Phase 5: Upload

General:
  F1              Open wizard
  F4              Show product family
  F5              Refresh
  Ctrl+Z          Undo
  Ctrl+G          Group by family
  Ctrl+L          Toggle log panel
  Escape          Stop current operation
  
View:
  Ctrl+Shift+C    Toggle advanced columns
"""
