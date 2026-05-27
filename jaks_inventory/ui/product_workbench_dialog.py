"""Product Workbench Dialog — unified Add / Edit product editor.

Replaces the legacy ``add_product_dialog.AddProductDialog`` and
``edit_product_dialog.EditProductDialog`` with a single workbench-style
dialog. Layout per ``mockups/product_workbench_plan.html``:

  ┌─ Titlebar / mode chip ──────────────────────────────────────┐
  ├─ Left rail (248px) ─┬─ Canvas (stretch) ─┬─ Meta rail (320) ┤
  │ filter + grouped   │ section cards       │ mode-specific    │
  │ tabs + progress    │ (Basic + Core       │ stats / preview  │
  │                    │ Charge always-on)   │ / AI insights    │
  ├────────────────────┴─────────────────────┴──────────────────┤
  └─ Sticky footer (mode-specific buttons) ─────────────────────┘

Mode accents:  new → teal ``#46c1ad``  ·  edit → olive ``#97a345``

This file is the **step-1 skeleton**: shared frame, mode swap, left-rail
tab list, empty section cards, footer wiring. Section bodies, the
tiered-pricing widget, and meta panels are populated in subsequent
steps.
"""

from __future__ import annotations

from typing import Literal, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QKeySequence, QShortcut, QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

try:  # DB lookup is optional in skeleton; tolerated when unavailable
    from db import get_product_by_sku, create_product, update_product
except Exception:  # pragma: no cover
    get_product_by_sku = None  # type: ignore
    create_product = None  # type: ignore
    update_product = None  # type: ignore

from .product_workbench_sections import (
    BasicSection,
    CoreChargeSection,
    PlatformSection,
    SupplierSection,
    WarrantySection,
    PricingSection,
    InventorySection,
    NotesSection,
    ShippingSection,
    CrossReferencesSection,
    SuggestedSellsSection,
    ImagesSection,
    ShopifySection,
)


# ── Palette (matches mockups/product_workbench_plan.html) ────────────────
_BG          = "#0c1116"
_PANEL       = "#131a22"
_PANEL2      = "#1a232d"
_PANEL3      = "#232f3b"
_BORDER      = "#2a3540"
_BORDER_HI   = "#3a4856"
_TEXT        = "#e6edf3"
_DIM         = "#8b98a5"
_FAINT       = "#5d6b78"

_ACCENT_NEW  = "#46c1ad"   # teal — Add mode
_ACCENT_EDIT = "#97a345"   # olive — Edit mode
_AI_PURPLE   = "#b06bff"
_GOLD        = "#ffc857"
_ORANGE      = "#ff8c42"

_GREEN       = "#3fb950"
_RED         = "#f85149"
_AMBER       = "#d29922"


# ── Left-rail section catalogue ──────────────────────────────────────────
# (group_label, [(section_key, display_label, required?), ...])
_SECTION_GROUPS: list[tuple[str, list[tuple[str, str, bool]]]] = [
    ("Always-on", [
        ("basic",       "Basic",       True),
        ("core_charge", "Core Charge", False),
    ]),
    ("Catalog", [
        ("platform",    "Platform",    False),
        ("supplier",    "Supplier / Vendor", True),
        ("warranty",    "Warranty",    False),
    ]),
    ("Commerce", [
        ("pricing",     "Pricing & Tiers", True),
        ("inventory",   "Inventory",   True),
        ("notes",       "Notes",       False),
        ("shipping",    "Shipping",    False),
    ]),
    ("Cross-sell", [
        ("xref",        "Cross-References", False),
        ("suggested",   "Suggested Sells", False),
    ]),
    ("Media & Channels", [
        ("images",      "Images",      False),
        ("shopify",     "Shopify",     False),
    ]),
]


def _flat_section_keys() -> list[str]:
    return [k for _grp, items in _SECTION_GROUPS for (k, _lbl, _req) in items]


# ── Stylesheet ───────────────────────────────────────────────────────────
def _qss(accent: str) -> str:
    return f"""
    QDialog {{
        background: {_BG};
        color: {_TEXT};
    }}

    /* Titlebar / header strip */
    #PWHeader {{
        background: {_PANEL};
        border-bottom: 1px solid {_BORDER};
    }}
    #PWHeaderTitle {{
        color: {_TEXT};
        font-size: 17px;
        font-weight: 600;
    }}
    #PWHeaderSubtitle {{
        color: {_DIM};
        font-size: 12px;
    }}
    #PWModeChip {{
        color: {_BG};
        background: {accent};
        border-radius: 11px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.6px;
    }}

    /* Rails */
    #PWLeftRail, #PWMetaRail {{
        background: {_PANEL};
        border-right: 1px solid {_BORDER};
    }}
    #PWMetaRail {{
        border-right: none;
        border-left: 1px solid {_BORDER};
    }}
    #PWLeftRail QLineEdit#PWFilter {{
        background: {_PANEL2};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        padding: 6px 10px;
        color: {_TEXT};
    }}
    #PWLeftRail QLineEdit#PWFilter:focus {{
        border: 1px solid {accent};
    }}
    #PWGroupLabel {{
        color: {_FAINT};
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 1.2px;
        padding: 10px 14px 4px 14px;
    }}

    /* Tab list */
    QListWidget#PWTabs {{
        background: transparent;
        border: none;
        outline: 0;
    }}
    QListWidget#PWTabs::item {{
        color: {_TEXT};
        padding: 8px 14px;
        border-left: 3px solid transparent;
    }}
    QListWidget#PWTabs::item:hover {{
        background: {_PANEL2};
    }}
    QListWidget#PWTabs::item:selected {{
        background: {_PANEL2};
        border-left: 3px solid {accent};
        color: {_TEXT};
    }}

    /* Canvas */
    QScrollArea#PWCanvas {{
        background: {_BG};
        border: none;
    }}
    QWidget#PWCanvasInner {{
        background: {_BG};
    }}

    /* Section cards */
    QFrame#PWSection {{
        background: {_PANEL};
        border: 1px solid {_BORDER};
        border-radius: 8px;
    }}
    QLabel#PWSectionTitle {{
        color: {_TEXT};
        font-size: 14px;
        font-weight: 600;
    }}
    QLabel#PWSectionHint {{
        color: {_DIM};
        font-size: 11px;
    }}
    QLabel#PWReqBadge {{
        color: {_BG};
        background: {accent};
        border-radius: 8px;
        padding: 2px 7px;
        font-size: 10px;
        font-weight: 700;
    }}
    QLabel#PWPlaceholder {{
        color: {_FAINT};
        font-size: 12px;
        padding: 24px;
    }}

    /* Footer */
    #PWFooter {{
        background: {_PANEL};
        border-top: 1px solid {_BORDER};
    }}
    QPushButton#PWBtn {{
        background: {_PANEL2};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        color: {_TEXT};
        padding: 7px 16px;
        font-size: 12px;
    }}
    QPushButton#PWBtn:hover {{
        background: {_PANEL3};
        border: 1px solid {_BORDER_HI};
    }}
    QPushButton#PWBtnPrimary {{
        background: {accent};
        border: 1px solid {accent};
        border-radius: 6px;
        color: {_BG};
        padding: 7px 18px;
        font-size: 12px;
        font-weight: 700;
    }}
    QPushButton#PWBtnPrimary:hover {{
        background: {_TEXT};
    }}
    QPushButton#PWBtnDanger {{
        background: transparent;
        border: 1px solid {_RED};
        border-radius: 6px;
        color: {_RED};
        padding: 7px 14px;
        font-size: 12px;
    }}
    QPushButton#PWBtnDanger:hover {{
        background: {_RED};
        color: {_BG};
    }}

    /* Progress mini */
    QProgressBar#PWProgress {{
        background: {_PANEL2};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        text-align: center;
        color: {_TEXT};
        font-size: 10px;
        max-height: 12px;
    }}
    QProgressBar#PWProgress::chunk {{
        background: {accent};
        border-radius: 3px;
    }}
    """


# ── Section card (wraps a section widget) ────────────────────────────────
class _SectionCard(QFrame):
    """Section card: header strip + body widget.

    If ``body`` is None the card renders a placeholder with an
    "Open in legacy editor" button for sections not yet ported.
    """

    def __init__(self, key: str, title: str, required: bool,
                 body: QWidget | None = None,
                 on_legacy: callable | None = None,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("PWSection")
        self.section_key = key
        self.body = body

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 20)
        v.setSpacing(10)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("PWSectionTitle")
        hdr.addWidget(title_lbl)
        if required:
            req = QLabel("REQUIRED")
            req.setObjectName("PWReqBadge")
            hdr.addWidget(req)
        hdr.addStretch(1)
        v.addLayout(hdr)

        if body is not None:
            v.addWidget(body)
        else:
            placeholder = QLabel(
                f"{title} is still served by the legacy editor.\n"
                "Click below to open the full editor for this section."
            )
            placeholder.setObjectName("PWPlaceholder")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setWordWrap(True)
            v.addWidget(placeholder)
            if on_legacy is not None:
                btn_row = QHBoxLayout()
                btn_row.addStretch(1)
                btn = QPushButton("Open in legacy editor")
                btn.setObjectName("PWBtn")
                btn.clicked.connect(lambda: on_legacy(key))
                btn_row.addWidget(btn)
                btn_row.addStretch(1)
                v.addLayout(btn_row)


# ── Main dialog ──────────────────────────────────────────────────────────
class ProductWorkbenchDialog(QDialog):
    """Unified Add / Edit Product dialog."""

    # Track open modeless dialogs to prevent garbage collection
    _open_dialogs: list = []

    def __init__(
        self,
        mode: Literal["new", "edit"] = "new",
        *,
        sku: Optional[str] = None,
        product_id: Optional[int] = None,
        initial_section: Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if mode not in ("new", "edit"):
            raise ValueError(f"mode must be 'new' or 'edit', got {mode!r}")
        self._mode: Literal["new", "edit"] = mode
        self._sku: str = sku or ""
        self._product_id: Optional[int] = product_id
        self._product: dict | None = None
        self._modified = False
        self._accent = _ACCENT_NEW if mode == "new" else _ACCENT_EDIT
        self._section_cards: dict[str, _SectionCard] = {}
        # Normalize initial_section: legacy callers used e.g. 'PRICING'
        self._initial_section: Optional[str] = (
            (initial_section or "").strip().lower() or None
        )
        # Map legacy section keys to workbench keys
        _legacy_map = {
            "basic": "basic",
            "core_charge": "core_charge",
            "core": "core_charge",
            "platform": "platform",
            "supplier": "supplier",
            "vendor": "supplier",
            "warranty": "warranty",
            "pricing": "pricing",
            "inventory": "inventory",
            "notes": "notes",
            "shipping": "shipping",
            "xref": "xref",
            "cross-references": "xref",
            "suggested": "suggested",
            "images": "images",
            "shopify": "shopify",
        }
        if self._initial_section:
            self._initial_section = _legacy_map.get(
                self._initial_section, self._initial_section
            )

        # Window chrome
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumSize(1200, 800)

        if mode == "edit":
            self.setWindowTitle(f"Edit Product: {self._sku}" if self._sku
                                else "Edit Product")
            self._load_product()
        else:
            self.setWindowTitle("New Product")

        self.setStyleSheet(_qss(self._accent))
        self._build_ui()
        self._install_shortcuts()
        self._populate_sections()
        self.showMaximized()
        # Jump to requested section after layout settles
        if self._initial_section:
            self._select_section(self._initial_section)

    def _select_section(self, key: str) -> None:
        """Programmatically select a section by key (matches a tab item)."""
        for i in range(self._tabs.count()):
            it = self._tabs.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == key:
                self._tabs.setCurrentItem(it)
                return

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _load_product(self) -> None:
        if not self._sku or get_product_by_sku is None:
            return
        try:
            self._product = get_product_by_sku(self._sku)
            if self._product:
                self._product_id = self._product.get("id")
        except Exception as exc:
            QMessageBox.warning(self, "Error",
                                f"Failed to load product: {exc}")
            self._product = {}

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_left_rail(), 0)
        body.addWidget(self._build_canvas(), 1)
        body.addWidget(self._build_meta_rail(), 0)
        body_w = QWidget()
        body_w.setLayout(body)
        root.addWidget(body_w, 1)

        root.addWidget(self._build_footer())

    # -- Header --------------------------------------------------------
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("PWHeader")
        bar.setFixedHeight(56)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 8, 20, 8)
        h.setSpacing(12)

        if self._mode == "new":
            title_text = "New Product"
            sub_text = "Create a new SKU"
            chip_text = "NEW"
        else:
            title_text = f"Edit Product · {self._sku}" if self._sku else "Edit Product"
            sub_text = (self._product or {}).get("description", "") or "—"
            chip_text = "EDIT"

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)
        title_lbl = QLabel(title_text)
        title_lbl.setObjectName("PWHeaderTitle")
        sub_lbl = QLabel(sub_text)
        sub_lbl.setObjectName("PWHeaderSubtitle")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)

        chip = QLabel(chip_text)
        chip.setObjectName("PWModeChip")

        # Escape hatch: open legacy editor for full set of fields
        legacy_btn = QPushButton("Open Full Editor…")
        legacy_btn.setObjectName("PWBtn")
        legacy_btn.setToolTip(
            "Open the legacy editor for sections not yet ported "
            "(Platform, Supplier, Warranty, Cross-refs, Images, Shopify…)"
        )
        legacy_btn.clicked.connect(lambda: self._open_legacy_section(None))

        h.addLayout(title_box)
        h.addStretch(1)
        h.addWidget(legacy_btn)
        h.addWidget(chip)
        return bar

    # -- Left rail -----------------------------------------------------
    def _build_left_rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("PWLeftRail")
        rail.setFixedWidth(248)

        v = QVBoxLayout(rail)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        self._filter = QLineEdit()
        self._filter.setObjectName("PWFilter")
        self._filter.setPlaceholderText("Filter sections…")
        self._filter.textChanged.connect(self._on_filter_changed)
        v.addWidget(self._filter)

        # Tab list (single QListWidget with non-selectable group headers)
        self._tabs = QListWidget()
        self._tabs.setObjectName("PWTabs")
        self._tabs.setFrameShape(QFrame.Shape.NoFrame)
        self._tabs.setSpacing(0)
        for group_label, items in _SECTION_GROUPS:
            grp = QListWidgetItem(group_label.upper())
            grp.setFlags(Qt.ItemFlag.NoItemFlags)  # not selectable
            f = QFont()
            f.setBold(True)
            f.setPointSize(8)
            grp.setFont(f)
            grp.setForeground(Qt.GlobalColor.gray)
            self._tabs.addItem(grp)
            for key, label, _req in items:
                it = QListWidgetItem(f"  {label}")
                it.setData(Qt.ItemDataRole.UserRole, key)
                self._tabs.addItem(it)
        self._tabs.currentItemChanged.connect(self._on_tab_changed)
        v.addWidget(self._tabs, 1)

        # Progress mini
        prog_box = QVBoxLayout()
        prog_box.setSpacing(4)
        if self._mode == "new":
            prog_lbl = QLabel("Setup progress")
        else:
            prog_lbl = QLabel("Listing health")
        prog_lbl.setStyleSheet(f"color: {_DIM}; font-size: 10px;")
        self._progress = QProgressBar()
        self._progress.setObjectName("PWProgress")
        self._progress.setRange(0, 100)
        self._progress.setValue(42 if self._mode == "new" else 92)
        prog_box.addWidget(prog_lbl)
        prog_box.addWidget(self._progress)
        v.addLayout(prog_box)

        return rail

    # -- Canvas --------------------------------------------------------
    def _build_canvas(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("PWCanvas")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setObjectName("PWCanvasInner")
        v = QVBoxLayout(inner)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(16)

        # Build the real section widgets keyed by section key
        self._sections: dict[str, object] = {
            "basic":            BasicSection(create_mode=(self._mode == "new")),
            "core_charge":      CoreChargeSection(),
            "platform":         PlatformSection(),
            "supplier":         SupplierSection(),
            "warranty":         WarrantySection(),
            "pricing":          PricingSection(),
            "inventory":        InventorySection(),
            "notes":            NotesSection(),
            "shipping":         ShippingSection(),
            "xref":             CrossReferencesSection(),
            "suggested":        SuggestedSellsSection(),
            "images":           ImagesSection(),
            "shopify":          ShopifySection(),
        }
        for body in self._sections.values():
            body.changed.connect(self._on_section_changed)  # type: ignore

        for _group, items in _SECTION_GROUPS:
            for key, label, required in items:
                body = self._sections.get(key)
                card = _SectionCard(
                    key, label, required, body=body,
                    on_legacy=self._open_legacy_section,
                )
                self._section_cards[key] = card
                v.addWidget(card)
        v.addStretch(1)

        scroll.setWidget(inner)
        self._canvas_scroll = scroll
        return scroll

    # -- Meta rail -----------------------------------------------------
    def _build_meta_rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("PWMetaRail")
        rail.setFixedWidth(320)
        v = QVBoxLayout(rail)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        if self._mode == "new":
            self._meta_panel: QWidget = _NewProductMetaPanel(self)
        else:
            self._meta_panel = _EditProductMetaPanel(self)
        v.addWidget(self._meta_panel)
        v.addStretch(1)
        return rail

    # -- Footer --------------------------------------------------------
    def _build_footer(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("PWFooter")
        bar.setFixedHeight(56)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 10, 20, 10)
        h.setSpacing(10)

        if self._mode == "new":
            cancel = QPushButton("Cancel")
            cancel.setObjectName("PWBtn")
            cancel.clicked.connect(self.reject)

            save_draft = QPushButton("Save Draft")
            save_draft.setObjectName("PWBtn")
            save_draft.clicked.connect(lambda: self._on_save(variant="draft"))

            save_add = QPushButton("Save && Add Another")
            save_add.setObjectName("PWBtn")
            save_add.clicked.connect(lambda: self._on_save(variant="add_another"))

            save_dup = QPushButton("Save && Duplicate")
            save_dup.setObjectName("PWBtn")
            save_dup.clicked.connect(lambda: self._on_save(variant="duplicate"))

            save_close = QPushButton("Save && Close")
            save_close.setObjectName("PWBtnPrimary")
            save_close.setDefault(True)
            save_close.clicked.connect(lambda: self._on_save(variant="close"))

            h.addWidget(cancel)
            h.addStretch(1)
            h.addWidget(save_draft)
            h.addWidget(save_add)
            h.addWidget(save_dup)
            h.addWidget(save_close)
        else:
            discard = QPushButton("Discard")
            discard.setObjectName("PWBtn")
            discard.clicked.connect(self.reject)

            duplicate = QPushButton("Duplicate")
            duplicate.setObjectName("PWBtn")
            duplicate.clicked.connect(self._on_duplicate)

            delete = QPushButton("Delete")
            delete.setObjectName("PWBtnDanger")
            delete.clicked.connect(self._on_delete)

            save = QPushButton("Save Changes")
            save.setObjectName("PWBtnPrimary")
            save.setDefault(True)
            save.clicked.connect(lambda: self._on_save(variant="close"))

            h.addWidget(discard)
            h.addStretch(1)
            h.addWidget(duplicate)
            h.addWidget(delete)
            h.addWidget(save)
        return bar

    # ------------------------------------------------------------------
    # Shortcuts
    # ------------------------------------------------------------------
    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+S"), self,
                  activated=lambda: self._on_save(variant="close"))
        QShortcut(QKeySequence("Ctrl+Return"), self,
                  activated=lambda: self._on_save(variant="close"))
        QShortcut(QKeySequence("Ctrl+Enter"), self,
                  activated=lambda: self._on_save(variant="close"))
        QShortcut(QKeySequence("Esc"), self, activated=self.reject)
        # Section navigation
        QShortcut(QKeySequence("Alt+Down"), self,
                  activated=lambda: self._step_section(1))
        QShortcut(QKeySequence("Alt+Up"), self,
                  activated=lambda: self._step_section(-1))
        # Focus filter
        QShortcut(QKeySequence("Ctrl+/"), self,
                  activated=lambda: self._filter.setFocus())
        QShortcut(QKeySequence("Ctrl+F"), self,
                  activated=lambda: self._filter.setFocus())
        # Group jumps: Ctrl+1 .. Ctrl+5 pick first section of each group
        for i, (_group, items) in enumerate(_SECTION_GROUPS, start=1):
            if i > 9 or not items:
                continue
            first_key = items[0][0]
            QShortcut(
                QKeySequence(f"Ctrl+{i}"), self,
                activated=lambda k=first_key: self._select_section(k),
            )

    def _step_section(self, delta: int) -> None:
        """Move selection up/down across selectable (non-group) tab rows."""
        if not hasattr(self, "_tabs"):
            return
        # Build list of (row_index, key) for selectable items only
        rows: list[tuple[int, str]] = []
        for i in range(self._tabs.count()):
            it = self._tabs.item(i)
            if not it or it.flags() == Qt.ItemFlag.NoItemFlags:
                continue
            key = it.data(Qt.ItemDataRole.UserRole)
            if key:
                rows.append((i, key))
        if not rows:
            return
        cur = self._tabs.currentRow()
        idx = 0
        for j, (row, _k) in enumerate(rows):
            if row == cur:
                idx = j
                break
        new_idx = (idx + delta) % len(rows)
        self._tabs.setCurrentRow(rows[new_idx][0])

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def _on_section_changed(self) -> None:
        self._modified = True
        self._refresh_meta_panel()

    def _populate_sections(self) -> None:
        """Push loaded product dict into every section widget."""
        product = self._product or {}
        for sec in getattr(self, "_sections", {}).values():
            try:
                sec.populate(product)  # type: ignore
            except Exception:
                pass
        self._modified = False
        self._refresh_meta_panel()

    def _refresh_meta_panel(self) -> None:
        panel = getattr(self, "_meta_panel", None)
        if panel is None:
            return
        try:
            panel.refresh(self._product or {}, self._sections, self._mode)
        except Exception:
            pass

    def _collect_changes(self) -> dict:
        data: dict = {}
        for sec in getattr(self, "_sections", {}).values():
            try:
                data.update(sec.collect())  # type: ignore
            except Exception:
                pass
        return data

    def _validate_all(self) -> list[str]:
        errs: list[str] = []
        for sec in getattr(self, "_sections", {}).values():
            try:
                errs.extend(sec.validate())  # type: ignore
            except Exception:
                pass
        return errs

    def _open_legacy_section(self, key: str | None) -> None:
        """Escape hatch: open the legacy EditProductDialog."""
        try:
            from .edit_product_dialog import EditProductDialog
        except Exception as exc:
            QMessageBox.warning(self, "Legacy editor unavailable",
                                f"Could not open legacy editor: {exc}")
            return
        if self._mode == "new" or not self._sku:
            # Open legacy in create-mode
            dlg = EditProductDialog(create_mode=True,
                                    initial_section=(key or "").upper() or None,
                                    parent=self)
        else:
            dlg = EditProductDialog(
                sku=self._sku,
                initial_section=(key or "").upper() or None,
                parent=self,
            )
        if dlg.exec():
            # Reload product so our sections reflect any changes made
            if self._mode == "edit":
                self._load_product()
                self._populate_sections()

    def _on_tab_changed(self, current, _previous) -> None:
        if current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        card = self._section_cards.get(key)
        if card is None:
            return
        # Scroll the canvas so the card top aligns with the viewport top
        self._canvas_scroll.ensureWidgetVisible(card, 0, 0)

    def _on_filter_changed(self, text: str) -> None:
        needle = text.strip().lower()
        # Hide/show non-group items based on filter text
        for i in range(self._tabs.count()):
            it = self._tabs.item(i)
            key = it.data(Qt.ItemDataRole.UserRole)
            if key is None:
                # group header — always visible
                it.setHidden(False)
                continue
            label = it.text().strip().lower()
            it.setHidden(bool(needle) and needle not in label)

    def _on_save(self, *, variant: str) -> None:
        errs = self._validate_all()
        if errs:
            QMessageBox.warning(self, "Validation",
                                "\n".join(f"• {e}" for e in errs))
            return
        data = self._collect_changes()
        # Filter to actual products-table columns to avoid OperationalError
        # on edit (update_product does not whitelist its kwargs).
        data = self._filter_to_products_columns(data)
        try:
            if self._mode == "new":
                if create_product is None:
                    raise RuntimeError("create_product unavailable")
                result = create_product(**data)
                if not result.get("success"):
                    raise RuntimeError(result.get("error", "Unknown error"))
                self._product_id = result.get("id")
                self._sku = str(data.get("sku") or "")
            else:
                if update_product is None or self._product_id is None:
                    raise RuntimeError("update_product unavailable")
                # Drop sku from updates (immutable in edit mode)
                data.pop("sku", None)
                update_product(self._product_id, **data)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return

        # Persist tier rows (separate table; can't go through update_product).
        try:
            pricing = self._sections.get("pricing")
            if pricing is not None and self._product_id and hasattr(
                    pricing, "save_tiers"):
                pricing.save_tiers(int(self._product_id))
        except Exception:
            pass

        self._modified = False
        if variant == "add_another":
            self.should_add_another = True
            self.accept()
        elif variant == "duplicate":
            self.should_add_another = True  # caller loops; same behaviour
            self.accept()
        elif variant == "draft":
            QMessageBox.information(self, "Saved",
                                    "Draft saved. Continue editing.")
        else:
            self.accept()

    should_add_another: bool = False

    _PRODUCTS_COLUMNS: set[str] | None = None

    @classmethod
    def _load_products_columns(cls) -> set[str]:
        if cls._PRODUCTS_COLUMNS is not None:
            return cls._PRODUCTS_COLUMNS
        cols: set[str] = set()
        try:
            from db import get_db
            with get_db() as conn:
                rows = conn.execute(
                    "PRAGMA table_info(products)"
                ).fetchall()
                for r in rows:
                    # Row could be sqlite3.Row or tuple
                    try:
                        cols.add(r[1])
                    except Exception:
                        try:
                            cols.add(r["name"])
                        except Exception:
                            pass
        except Exception:
            pass
        cls._PRODUCTS_COLUMNS = cols
        return cols

    def _filter_to_products_columns(self, data: dict) -> dict:
        """Drop keys that aren't real columns on the products table.

        ``update_product`` builds its UPDATE statement directly from the
        kwargs supplied (no whitelist), so unknown columns raise
        ``sqlite3.OperationalError``. Many of our section fields are
        forward-looking placeholders the schema doesn't carry yet —
        filter them out here so save never crashes.
        """
        cols = self._load_products_columns()
        if not cols:
            return data
        return {k: v for k, v in data.items() if k in cols}

    def _on_duplicate(self) -> None:  # pragma: no cover - stub
        QMessageBox.information(self, "Not yet wired",
                                "Duplicate — wired in a later step.")

    def _on_delete(self) -> None:  # pragma: no cover - stub
        QMessageBox.information(self, "Not yet wired",
                                "Delete — wired in a later step.")

# ── Meta panels ──────────────────────────────────────────────────────────
class _NewProductMetaPanel(QFrame):
    """Right-rail panel for NEW mode — completion + AI hints."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("PWMetaPanel")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        head = QLabel("COMPLETION")
        head.setStyleSheet(
            f"color: {_FAINT}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 1.2px;"
        )
        v.addWidget(head)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")
        self.bar.setFixedHeight(10)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:{_PANEL2}; border:1px solid {_BORDER};"
            f" border-radius:5px; color:{_TEXT}; font-size:9px; }}"
            f"QProgressBar::chunk {{ background:{_ACCENT_NEW}; border-radius:4px; }}"
        )
        v.addWidget(self.bar)

        self.summary = QLabel("0 of 13 sections")
        self.summary.setStyleSheet(f"color:{_DIM}; font-size: 11px;")
        v.addWidget(self.summary)

        v.addSpacing(8)
        sub = QLabel("REQUIRED")
        sub.setStyleSheet(
            f"color: {_FAINT}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 1.2px;"
        )
        v.addWidget(sub)

        self.checklist = QLabel()
        self.checklist.setWordWrap(True)
        self.checklist.setTextFormat(Qt.TextFormat.RichText)
        self.checklist.setStyleSheet(f"color:{_TEXT}; font-size: 12px;")
        v.addWidget(self.checklist)

        v.addSpacing(8)
        ai_head = QLabel("AI SUGGESTIONS")
        ai_head.setStyleSheet(
            f"color: {_AI_PURPLE}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 1.2px;"
        )
        v.addWidget(ai_head)

        self.ai = QLabel("Fill out the title and SKU to see suggestions.")
        self.ai.setWordWrap(True)
        self.ai.setStyleSheet(
            f"color:{_DIM}; font-size: 11px; background:{_PANEL2};"
            f" border:1px solid {_BORDER}; border-radius:6px; padding:8px;"
        )
        v.addWidget(self.ai)

    def refresh(self, product: dict, sections: dict, mode: str) -> None:
        total = max(1, len(sections))
        filled = 0
        required_status: list[tuple[str, bool]] = []
        for key, sec in sections.items():
            try:
                data = sec.collect()  # type: ignore[attr-defined]
            except Exception:
                data = {}
            non_empty = any(
                (v not in (None, "", 0, 0.0))
                for v in data.values()
            )
            if non_empty:
                filled += 1
            if getattr(sec, "is_required", False):
                ok = not sec.validate() if hasattr(sec, "validate") else non_empty
                title = getattr(sec, "section_title", key)
                required_status.append((title, bool(ok)))

        pct = int(round(100 * filled / total))
        self.bar.setValue(pct)
        self.summary.setText(f"{filled} of {total} sections filled")

        if required_status:
            lines = []
            for title, ok in required_status:
                mark = (f'<span style="color:{_ACCENT_NEW};">✓</span>' if ok
                        else f'<span style="color:{_DIM};">○</span>')
                lines.append(f"{mark} {title}")
            self.checklist.setText("<br>".join(lines))
        else:
            self.checklist.setText(f"<span style='color:{_DIM};'>None</span>")

        basic = sections.get("basic")
        title = ""
        if basic is not None:
            try:
                title = (basic.collect() or {}).get("title", "")  # type: ignore
            except Exception:
                title = ""
        if title and len(title) >= 8:
            self.ai.setText(
                f"Title looks good. Consider adding cross-references "
                f"and at least one image before publishing."
            )
        else:
            self.ai.setText(
                "Fill out the title and SKU to see suggestions."
            )


class _EditProductMetaPanel(QFrame):
    """Right-rail panel for EDIT mode — quick stats + activity."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("PWMetaPanel")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        head = QLabel("QUICK STATS")
        head.setStyleSheet(
            f"color: {_FAINT}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 1.2px;"
        )
        v.addWidget(head)

        self.stats = QLabel()
        self.stats.setTextFormat(Qt.TextFormat.RichText)
        self.stats.setWordWrap(True)
        self.stats.setStyleSheet(
            f"color:{_TEXT}; font-size: 12px; background:{_PANEL2};"
            f" border:1px solid {_BORDER}; border-radius:6px; padding:10px;"
        )
        v.addWidget(self.stats)

        v.addSpacing(6)
        head2 = QLabel("MARGIN")
        head2.setStyleSheet(
            f"color: {_FAINT}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 1.2px;"
        )
        v.addWidget(head2)
        self.margin = QLabel("—")
        self.margin.setStyleSheet(
            f"color:{_TEXT}; font-size: 18px; font-weight:600;"
        )
        v.addWidget(self.margin)

        v.addSpacing(6)
        head3 = QLabel("ACTIVITY")
        head3.setStyleSheet(
            f"color: {_FAINT}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 1.2px;"
        )
        v.addWidget(head3)
        self.activity = QLabel("—")
        self.activity.setWordWrap(True)
        self.activity.setStyleSheet(
            f"color:{_DIM}; font-size: 11px; background:{_PANEL2};"
            f" border:1px solid {_BORDER}; border-radius:6px; padding:8px;"
        )
        v.addWidget(self.activity)

    def refresh(self, product: dict, sections: dict, mode: str) -> None:
        if not product:
            self.stats.setText(f"<span style='color:{_DIM};'>No product loaded.</span>")
            self.margin.setText("—")
            self.activity.setText("—")
            return

        qty = product.get("quantity_on_hand", 0) or 0
        cost = float(product.get("cost") or 0)
        pai_cost = float(product.get("pai_cost") or 0)
        eff_cost = pai_cost if pai_cost > 0 else cost
        price = float(product.get("selling_price") or 0)
        value = eff_cost * (qty or 0)
        sku = product.get("sku", "")
        status = product.get("status", "") or product.get("shopify_status", "") or "—"
        updated = product.get("updated_at", "") or product.get("modified_at", "") or ""

        rows = [
            f"<b>{sku}</b>",
            f"<span style='color:{_DIM};'>Status:</span> {status}",
            f"<span style='color:{_DIM};'>On hand:</span> {qty}",
            f"<span style='color:{_DIM};'>Inv value:</span> ${value:,.2f}",
            f"<span style='color:{_DIM};'>Price:</span> ${price:,.2f}",
            f"<span style='color:{_DIM};'>Cost:</span> ${eff_cost:,.2f}",
        ]
        if updated:
            rows.append(f"<span style='color:{_DIM};'>Updated:</span> {updated}")
        self.stats.setText("<br>".join(rows))

        if price > 0 and eff_cost > 0:
            margin_pct = (price - eff_cost) / price * 100.0
            colour = _ACCENT_EDIT if margin_pct >= 25 else (_GOLD if margin_pct >= 10 else "#f85149")
            self.margin.setText(
                f"<span style='color:{colour};'>{margin_pct:.1f}%</span>"
            )
        else:
            self.margin.setText(f"<span style='color:{_DIM};'>—</span>")

        # Activity placeholder: counts of related rows when available
        from contextlib import suppress
        img_n = xref_n = ss_n = None
        with suppress(Exception):
            from db import get_product_images as _gi
            img_n = len(_gi(product.get("id")) or []) if product.get("id") else 0
        with suppress(Exception):
            from db import get_interchanges as _gx
            xref_n = len(_gx(product.get("id")) or []) if product.get("id") else 0
        with suppress(Exception):
            from db import get_suggested_sells as _gs
            ss_n = len(_gs(product.get("id")) or []) if product.get("id") else 0

        parts = []
        if img_n is not None:
            parts.append(f"{img_n} image{'s' if img_n != 1 else ''}")
        if xref_n is not None:
            parts.append(f"{xref_n} cross-ref{'s' if xref_n != 1 else ''}")
        if ss_n is not None:
            parts.append(f"{ss_n} suggested sell{'s' if ss_n != 1 else ''}")
        self.activity.setText(" · ".join(parts) if parts else "—")

__all__ = ["ProductWorkbenchDialog"]
