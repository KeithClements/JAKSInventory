"""Section widgets for ProductWorkbenchDialog.

Each section is a self-contained QWidget exposing:
  - ``populate(product: dict)`` — load values from a product row.
  - ``collect() -> dict`` — return DB-shaped kwargs for create/update.
  - ``validate() -> list[str]`` — return list of error messages (empty = OK).

Sections implemented in this iteration:
  BasicSection · CoreChargeSection · PricingSection · InventorySection
  · NotesSection · ShippingSection
plus the visual PricingTiersTable widget consumed by PricingSection.

Remaining sections (Platform, Supplier, Warranty, Xref, Suggested,
Images, Shopify) still use the legacy editor — accessed via the
workbench's "Open Full Editor" escape hatch.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Shared palette (mirrors product_workbench_dialog.py)
_TEXT       = "#e6edf3"
_DIM        = "#8b98a5"
_FAINT      = "#5d6b78"
_BORDER     = "#2a3540"
_PANEL2     = "#1a232d"
_PANEL3     = "#232f3b"
_GREEN      = "#3fb950"
_RED        = "#f85149"
_AMBER      = "#d29922"
_AI_PURPLE  = "#b06bff"

try:
    from db import list_price_categories
except Exception:  # pragma: no cover
    list_price_categories = None  # type: ignore

try:
    from db import get_product_qty_tiers, set_product_qty_tiers
except Exception:  # pragma: no cover
    get_product_qty_tiers = None  # type: ignore
    set_product_qty_tiers = None  # type: ignore

try:
    from db import (
        get_all_vendors,
        get_all_engine_manufacturers,
        get_all_engine_models,
    )
except Exception:  # pragma: no cover
    get_all_vendors = None  # type: ignore
    get_all_engine_manufacturers = None  # type: ignore
    get_all_engine_models = None  # type: ignore

try:
    from db import (
        get_interchanges, add_interchange, delete_interchange,
        get_product_images, add_product_image, delete_product_image,
        get_suggested_sells, add_suggested_sell, delete_suggested_sell,
        search_by_part_number,
    )
except Exception:  # pragma: no cover
    get_interchanges = add_interchange = delete_interchange = None  # type: ignore
    get_product_images = add_product_image = delete_product_image = None  # type: ignore
    get_suggested_sells = add_suggested_sell = delete_suggested_sell = None  # type: ignore
    search_by_part_number = None  # type: ignore


# ── Common helpers ───────────────────────────────────────────────────────
def _label(text: str, *, dim: bool = False, small: bool = False) -> QLabel:
    lbl = QLabel(text)
    style = []
    if dim:
        style.append(f"color: {_DIM};")
    if small:
        style.append("font-size: 10px;")
    if style:
        lbl.setStyleSheet(" ".join(style))
    return lbl


def _line_edit(placeholder: str = "") -> QLineEdit:
    e = QLineEdit()
    if placeholder:
        e.setPlaceholderText(placeholder)
    return e


def _spin(min_val: int = 0, max_val: int = 999_999, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(min_val, max_val)
    if suffix:
        s.setSuffix(suffix)
    return s


def _dspin(min_val: float = 0.0, max_val: float = 1_000_000.0,
           decimals: int = 2, prefix: str = "") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(min_val, max_val)
    s.setDecimals(decimals)
    if prefix:
        s.setPrefix(prefix)
    return s


# ── BASE class ───────────────────────────────────────────────────────────
class _SectionBase(QWidget):
    """Base for section widgets — provides common signals/contract."""

    changed = Signal()

    section_key: str = ""
    section_title: str = ""
    is_required: bool = False

    def populate(self, product: dict) -> None:
        """Load values from a product row. Override in subclasses."""

    def collect(self) -> dict[str, Any]:
        """Return kwargs for create_product / update_product."""
        return {}

    def validate(self) -> list[str]:
        return []


# ── 1. BASIC ─────────────────────────────────────────────────────────────
class BasicSection(_SectionBase):
    section_key = "basic"
    section_title = "Basic"
    is_required = True

    def __init__(self, *, create_mode: bool, parent=None):
        super().__init__(parent)
        self._create_mode = create_mode
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        self.sku = _line_edit("SKU (e.g. JAK-12345)")
        self.title = _line_edit("Brief description / title")
        self.oem_pn = _line_edit("OEM part number")
        self.condition = QComboBox()
        self.condition.addItems(["New", "Remanufactured", "Used", "NOS", "Refurbished"])
        self.description_html = QPlainTextEdit()
        self.description_html.setPlaceholderText(
            "Long description (plain text or HTML)…"
        )
        self.description_html.setFixedHeight(110)

        self.sku.setReadOnly(not create_mode)
        if not create_mode:
            self.sku.setStyleSheet(f"color: {_DIM};")

        form.addRow("SKU *", self.sku)
        form.addRow("Title *", self.title)
        form.addRow("Condition", self.condition)
        form.addRow("OEM Part #", self.oem_pn)
        form.addRow("Description", self.description_html)

        for w in (self.sku, self.title, self.oem_pn):
            w.textChanged.connect(self.changed)
        self.condition.currentIndexChanged.connect(self.changed)
        self.description_html.textChanged.connect(self.changed)

    def populate(self, p: dict) -> None:
        self.sku.setText(str(p.get("sku") or ""))
        self.title.setText(str(p.get("title") or p.get("brief_description") or ""))
        self.oem_pn.setText(str(p.get("oem_part_number") or ""))
        cond = str(p.get("condition") or "New").strip()
        idx = self.condition.findText(cond, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.condition.setCurrentIndex(idx)
        self.description_html.setPlainText(str(p.get("description_html") or ""))

    def collect(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "title": self.title.text().strip(),
            "brief_description": self.title.text().strip(),
            "oem_part_number": self.oem_pn.text().strip(),
            "condition": self.condition.currentText(),
            "description_html": self.description_html.toPlainText(),
        }
        if self._create_mode:
            data["sku"] = self.sku.text().strip()
        return data

    def validate(self) -> list[str]:
        errs = []
        if self._create_mode and not self.sku.text().strip():
            errs.append("SKU is required.")
        if not self.title.text().strip():
            errs.append("Title / brief description is required.")
        return errs


# ── 2. CORE CHARGE ───────────────────────────────────────────────────────
class CoreChargeSection(_SectionBase):
    section_key = "core_charge"
    section_title = "Core Charge"

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        self.has_core = QCheckBox("This product has a core charge")
        v.addWidget(self.has_core)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.core_sell_price = _dspin(prefix="$ ")
        self.core_charge = _dspin(prefix="$ ")
        self.core_return_days = _spin(0, 365, " days")
        self.core_return_days.setValue(30)
        self.core_notes = QPlainTextEdit()
        self.core_notes.setPlaceholderText("Notes about the core (optional)…")
        self.core_notes.setFixedHeight(70)

        form.addRow("Customer core charge", self.core_sell_price)
        form.addRow("Vendor core deposit", self.core_charge)
        form.addRow("Return window", self.core_return_days)
        form.addRow("Notes", self.core_notes)

        self._details = QWidget()
        self._details.setLayout(form)
        v.addWidget(self._details)

        self.margin_label = _label("Core margin: —", dim=True, small=True)
        v.addWidget(self.margin_label)

        self.has_core.toggled.connect(self._on_toggle)
        self.has_core.toggled.connect(self.changed)
        for w in (self.core_sell_price, self.core_charge):
            w.valueChanged.connect(self.changed)
            w.valueChanged.connect(self._update_margin)
        self.core_return_days.valueChanged.connect(self.changed)
        self.core_notes.textChanged.connect(self.changed)
        self._on_toggle(False)

    def _on_toggle(self, on: bool) -> None:
        self._details.setEnabled(on)
        self.margin_label.setVisible(on)

    def _update_margin(self) -> None:
        sell = self.core_sell_price.value()
        cost = self.core_charge.value()
        if sell <= 0:
            self.margin_label.setText("Core margin: —")
            return
        margin = sell - cost
        pct = (margin / sell * 100.0) if sell else 0.0
        color = _GREEN if margin >= 0 else _RED
        self.margin_label.setText(
            f"Core margin: <span style='color:{color}'>"
            f"${margin:,.2f} ({pct:+.1f}%)</span>"
        )
        self.margin_label.setTextFormat(Qt.TextFormat.RichText)

    def populate(self, p: dict) -> None:
        self.has_core.setChecked(bool(p.get("has_core")))
        self.core_sell_price.setValue(float(p.get("core_sell_price") or 0))
        self.core_charge.setValue(float(p.get("core_charge") or 0))
        self.core_return_days.setValue(int(p.get("core_return_days") or 30))
        self.core_notes.setPlainText(str(p.get("core_notes") or ""))
        self._update_margin()

    def collect(self) -> dict[str, Any]:
        return {
            # has_core column may not exist in schema's allowed list — keep
            # only safe columns (core_sell_price + core_charge are allowed
            # in create_product's whitelist).
            "core_sell_price": self.core_sell_price.value() if self.has_core.isChecked() else 0,
            "core_charge": self.core_charge.value() if self.has_core.isChecked() else 0,
        }

    def validate(self) -> list[str]:
        if not self.has_core.isChecked():
            return []
        errs = []
        if self.core_sell_price.value() <= 0:
            errs.append("Core charge enabled but customer core price is $0.")
        return errs


# ── PricingTiersTable (visual widget; in-memory tiers) ───────────────────
class PricingTiersTable(QWidget):
    """Per-product quantity-tier table.

    For now tiers are kept in memory only; persistence is planned in a
    follow-up step (likely a new ``product_qty_tiers`` table).
    """

    changed = Signal()

    _DEFAULT_NAMES = ["Retail", "Dealer", "Fleet", "Wholesale",
                      "Distributor"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tiers: list[dict] = []
        self._cost: float = 0.0

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # Header strip
        top = QHBoxLayout()
        top.setSpacing(8)
        title = _label("Quantity Tiers", small=False)
        title.setStyleSheet(f"color: {_TEXT}; font-weight: 600;")
        top.addWidget(title)
        top.addStretch(1)
        suggest = QPushButton("✨ Suggest tiers")
        suggest.setStyleSheet(
            f"background: transparent; border: 1px solid {_AI_PURPLE};"
            f" color: {_AI_PURPLE}; border-radius: 4px;"
            f" padding: 3px 10px; font-size: 11px;"
        )
        suggest.clicked.connect(self._suggest_tiers)
        top.addWidget(suggest)
        v.addLayout(top)

        # Table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Tier", "Min Qty", "Price", "Discount", "Margin"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet(
            f"QTableWidget {{ background: {_PANEL2}; color: {_TEXT};"
            f" gridline-color: {_BORDER}; border: 1px solid {_BORDER};"
            f" border-radius: 6px; }}"
            f"QHeaderView::section {{ background: {_PANEL3};"
            f" color: {_DIM}; border: none; padding: 4px 8px;"
            f" font-size: 11px; font-weight: 600; }}"
        )
        v.addWidget(self.table)

        # Actions row
        row = QHBoxLayout()
        row.setSpacing(6)
        add_btn = QPushButton("+ Add tier")
        add_btn.clicked.connect(lambda: self._add_tier())
        rm_btn = QPushButton("− Remove")
        rm_btn.clicked.connect(self._remove_current)
        for b in (add_btn, rm_btn):
            b.setStyleSheet(
                f"background: {_PANEL3}; color: {_TEXT};"
                f" border: 1px solid {_BORDER}; border-radius: 4px;"
                f" padding: 4px 12px; font-size: 11px;"
            )
        row.addWidget(add_btn)
        row.addWidget(rm_btn)
        row.addStretch(1)

        self.warn = _label("", small=True)
        self.warn.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.warn)
        v.addLayout(row)

        self.table.itemChanged.connect(self._on_item_changed)

    # -- API ----------------------------------------------------------
    def set_cost(self, cost: float) -> None:
        self._cost = float(cost or 0)
        self._refresh_all_margins()

    def set_base_price(self, price: float) -> None:
        """Recompute discounts relative to the new base price."""
        if not self._tiers:
            return
        for i, t in enumerate(self._tiers):
            if price > 0 and t.get("price"):
                t["discount"] = (1 - t["price"] / price) * 100.0
            self._render_row(i)

    def tiers(self) -> list[dict]:
        return [dict(t) for t in self._tiers]

    def load_tiers(self, tiers: list[dict]) -> None:
        self._tiers = [dict(t) for t in (tiers or [])]
        self._render_all()

    # -- Internals ----------------------------------------------------
    def _next_default_name(self) -> str:
        used = {t["name"] for t in self._tiers}
        for n in self._DEFAULT_NAMES:
            if n not in used:
                return n
        return f"Custom {len(self._tiers) - len(self._DEFAULT_NAMES) + 1}"

    def _add_tier(self, *, name: str | None = None, min_qty: int = 1,
                  price: float = 0.0) -> None:
        self._tiers.append({
            "name": name or self._next_default_name(),
            "min_qty": min_qty,
            "price": price,
            "discount": 0.0,
        })
        self._render_all()
        self.changed.emit()

    def _remove_current(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._tiers):
            del self._tiers[row]
            self._render_all()
            self.changed.emit()

    def _suggest_tiers(self) -> None:
        if self._tiers:
            return
        # Sensible defaults if a cost is known
        base = max(self._cost * 1.5, 50.0)
        for i, (name, qty, disc) in enumerate([
            ("Retail", 1, 0.0),
            ("Dealer", 5, 0.10),
            ("Fleet", 10, 0.15),
            ("Wholesale", 25, 0.22),
        ]):
            self._tiers.append({
                "name": name, "min_qty": qty,
                "price": round(base * (1 - disc), 2),
                "discount": disc * 100.0,
            })
        self._render_all()
        self.changed.emit()

    def _render_all(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._tiers))
        for i in range(len(self._tiers)):
            self._render_row(i)
        self.table.blockSignals(False)
        self._refresh_warn()

    def _render_row(self, i: int) -> None:
        t = self._tiers[i]
        self.table.blockSignals(True)
        self.table.setItem(i, 0, QTableWidgetItem(str(t.get("name", ""))))
        self.table.setItem(i, 1, QTableWidgetItem(str(int(t.get("min_qty") or 0))))
        self.table.setItem(i, 2, QTableWidgetItem(f"{float(t.get('price') or 0):.2f}"))
        disc_item = QTableWidgetItem(f"{float(t.get('discount') or 0):.1f}%")
        disc_item.setFlags(disc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(i, 3, disc_item)
        margin_item = QTableWidgetItem(self._margin_text(t))
        margin_item.setFlags(margin_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(i, 4, margin_item)
        self.table.blockSignals(False)

    def _margin_text(self, t: dict) -> str:
        price = float(t.get("price") or 0)
        if price <= 0 or self._cost <= 0:
            return "—"
        pct = (price - self._cost) / price * 100.0
        return f"{pct:+.1f}%"

    def _refresh_all_margins(self) -> None:
        for i in range(len(self._tiers)):
            self._render_row(i)
        self._refresh_warn()

    def _refresh_warn(self) -> None:
        if not self._tiers or self._cost <= 0:
            self.warn.setText("")
            return
        bad = [t for t in self._tiers
               if t.get("price", 0) and t["price"] < self._cost]
        if bad:
            self.warn.setText(
                f"<span style='color:{_RED}'>"
                f"⚠ {len(bad)} tier(s) priced below cost</span>"
            )
        else:
            self.warn.setText(
                f"<span style='color:{_GREEN}'>✓ all tiers above cost</span>"
            )

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row, col = item.row(), item.column()
        if row >= len(self._tiers):
            return
        t = self._tiers[row]
        text = item.text().strip()
        try:
            if col == 0:
                t["name"] = text
            elif col == 1:
                t["min_qty"] = int(float(text or 0))
            elif col == 2:
                t["price"] = float(text or 0)
        except ValueError:
            self._render_row(row)
            return
        self._render_row(row)
        self._refresh_warn()
        self.changed.emit()


# ── 6. PRICING ───────────────────────────────────────────────────────────
class PricingSection(_SectionBase):
    section_key = "pricing"
    section_title = "Pricing & Tiers"
    is_required = True

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # Price floor / cost basis row (4 cols)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.addWidget(_label("Cost", dim=True, small=True), 0, 0)
        grid.addWidget(_label("PAI / Vendor Cost", dim=True, small=True), 0, 1)
        grid.addWidget(_label("Selling Price", dim=True, small=True), 0, 2)
        grid.addWidget(_label("Compare-at", dim=True, small=True), 0, 3)
        self.cost = _dspin(prefix="$ ")
        self.pai_cost = _dspin(prefix="$ ")
        self.selling_price = _dspin(prefix="$ ")
        self.compare_at = _dspin(prefix="$ ")
        grid.addWidget(self.cost, 1, 0)
        grid.addWidget(self.pai_cost, 1, 1)
        grid.addWidget(self.selling_price, 1, 2)
        grid.addWidget(self.compare_at, 1, 3)
        v.addLayout(grid)

        # Margin display row
        margin_row = QHBoxLayout()
        margin_row.setSpacing(16)
        self.margin_label = _label("Margin: —", dim=True, small=True)
        self.margin_label.setTextFormat(Qt.TextFormat.RichText)
        margin_row.addWidget(self.margin_label)
        margin_row.addStretch(1)
        # Price category
        margin_row.addWidget(_label("Category:", dim=True, small=True))
        self.category_combo = QComboBox()
        self.category_combo.setMinimumWidth(180)
        self._load_categories()
        margin_row.addWidget(self.category_combo)
        v.addLayout(margin_row)

        # Tier table
        self.tiers = PricingTiersTable()
        v.addWidget(self.tiers)

        # Signals
        for s in (self.cost, self.pai_cost, self.selling_price,
                  self.compare_at):
            s.valueChanged.connect(self.changed)
        self.cost.valueChanged.connect(
            lambda val: self.tiers.set_cost(val)
        )
        self.cost.valueChanged.connect(self._update_margin)
        self.selling_price.valueChanged.connect(self._update_margin)
        self.selling_price.valueChanged.connect(
            lambda val: self.tiers.set_base_price(val)
        )
        self.category_combo.currentIndexChanged.connect(self.changed)
        self.tiers.changed.connect(self.changed)

    def _load_categories(self) -> None:
        self.category_combo.clear()
        self.category_combo.addItem("— Auto (use vendor default) —", None)
        if list_price_categories is None:
            return
        try:
            for c in list_price_categories(active_only=True):
                self.category_combo.addItem(
                    f"{c.get('code', '')} · {c.get('name', '')}",
                    c.get("id"),
                )
        except Exception:
            pass

    def _update_margin(self) -> None:
        sell = self.selling_price.value()
        cost = self.cost.value()
        if sell <= 0 or cost <= 0:
            self.margin_label.setText("Margin: —")
            return
        margin = sell - cost
        pct = margin / sell * 100.0
        color = (_GREEN if pct >= 25 else _AMBER if pct >= 10 else _RED)
        self.margin_label.setText(
            f"Margin: <span style='color:{color}'>"
            f"${margin:,.2f} ({pct:.1f}%)</span>"
        )

    def populate(self, p: dict) -> None:
        self.cost.setValue(float(p.get("cost") or 0))
        self.pai_cost.setValue(float(p.get("pai_cost") or 0))
        self.selling_price.setValue(float(p.get("selling_price") or 0))
        self.compare_at.setValue(float(p.get("compare_at_price") or 0))
        cat_id = p.get("price_category_id")
        if cat_id is not None:
            for i in range(self.category_combo.count()):
                if self.category_combo.itemData(i) == cat_id:
                    self.category_combo.setCurrentIndex(i)
                    break
        self.tiers.set_cost(self.cost.value())
        # Load tier rows for an existing product (NEW mode has no id yet).
        pid = p.get("id")
        if pid and get_product_qty_tiers is not None:
            try:
                rows = get_product_qty_tiers(int(pid)) or []
                # DB schema uses ``discount_pct``; widget uses ``discount``.
                mapped = [
                    {
                        "name": r.get("name") or "",
                        "min_qty": int(r.get("min_qty") or 0),
                        "price": float(r.get("price") or 0),
                        "discount": float(r.get("discount_pct") or 0),
                    }
                    for r in rows
                ]
                self.tiers.load_tiers(mapped)
            except Exception:
                pass
        self._update_margin()

    def collect(self) -> dict[str, Any]:
        return {
            "cost": self.cost.value(),
            "pai_cost": self.pai_cost.value(),
            "selling_price": self.selling_price.value(),
            "compare_at_price": self.compare_at.value(),
            "price_category_id": self.category_combo.currentData(),
        }

    def save_tiers(self, product_id: int) -> bool:
        """Persist the current tier table to ``product_qty_tiers``.

        Called by the dialog after create/update so tiers can attach to
        the resolved product_id (which may not exist during ``collect``).
        """
        if not product_id or set_product_qty_tiers is None:
            return False
        try:
            rows = self.tiers.tiers()
            mapped = [
                {
                    "name": t.get("name") or "",
                    "min_qty": int(t.get("min_qty") or 0),
                    "price": float(t.get("price") or 0),
                    "discount_pct": float(t.get("discount") or 0),
                    "sort_order": i,
                }
                for i, t in enumerate(rows)
            ]
            return bool(set_product_qty_tiers(int(product_id), mapped))
        except Exception:
            return False

    def validate(self) -> list[str]:
        errs = []
        if self.selling_price.value() <= 0:
            errs.append("Selling price must be greater than $0.")
        return errs


# ── 7. INVENTORY ─────────────────────────────────────────────────────────
class InventorySection(_SectionBase):
    section_key = "inventory"
    section_title = "Inventory"
    is_required = True

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.category = _line_edit("e.g. Filters, Sensors")
        self.subcategory = _line_edit("optional")
        self.product_type = QComboBox()
        self.product_type.addItems(["inventoried", "non-inventoried", "service"])
        self.min_qty = _spin(0, 100_000)
        self.max_qty = _spin(0, 100_000)
        self.bin_location = _line_edit("e.g. A-12-3")
        self.requires_serial = QCheckBox("Track serial numbers")

        form.addRow("Category", self.category)
        form.addRow("Subcategory", self.subcategory)
        form.addRow("Type", self.product_type)
        form.addRow("Min Qty", self.min_qty)
        form.addRow("Max Qty", self.max_qty)
        form.addRow("Bin location", self.bin_location)
        form.addRow("", self.requires_serial)

        for w in (self.category, self.subcategory, self.bin_location):
            w.textChanged.connect(self.changed)
        self.product_type.currentIndexChanged.connect(self.changed)
        for w in (self.min_qty, self.max_qty):
            w.valueChanged.connect(self.changed)
        self.requires_serial.toggled.connect(self.changed)

    def populate(self, p: dict) -> None:
        self.category.setText(str(p.get("category") or ""))
        self.subcategory.setText(str(p.get("subcategory") or ""))
        pt = str(p.get("product_type") or "inventoried")
        idx = self.product_type.findText(pt)
        if idx >= 0:
            self.product_type.setCurrentIndex(idx)
        self.min_qty.setValue(int(p.get("min_qty") or 0))
        self.max_qty.setValue(int(p.get("max_qty") or 0))
        self.bin_location.setText(str(p.get("bin_location") or ""))
        self.requires_serial.setChecked(bool(p.get("requires_serial")))

    def collect(self) -> dict[str, Any]:
        return {
            "category": self.category.text().strip(),
            "subcategory": self.subcategory.text().strip(),
            "min_qty": self.min_qty.value(),
            "max_qty": self.max_qty.value(),
            "bin_location": self.bin_location.text().strip(),
            "requires_serial": 1 if self.requires_serial.isChecked() else 0,
        }


# ── 8. NOTES ─────────────────────────────────────────────────────────────
class NotesSection(_SectionBase):
    section_key = "notes"
    section_title = "Notes"

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        v.addWidget(_label("Public notes (shown to customers)", dim=True, small=True))
        self.public = QPlainTextEdit()
        self.public.setFixedHeight(80)
        v.addWidget(self.public)

        v.addWidget(_label("Internal notes (staff only)", dim=True, small=True))
        self.internal = QPlainTextEdit()
        self.internal.setFixedHeight(80)
        v.addWidget(self.internal)

        self.public.textChanged.connect(self.changed)
        self.internal.textChanged.connect(self.changed)

    def populate(self, p: dict) -> None:
        self.public.setPlainText(str(p.get("notes_public") or ""))
        self.internal.setPlainText(str(p.get("notes_internal") or ""))

    def collect(self) -> dict[str, Any]:
        return {
            "notes_public": self.public.toPlainText(),
            "notes_internal": self.internal.toPlainText(),
        }


# ── 9. SHIPPING ──────────────────────────────────────────────────────────
class ShippingSection(_SectionBase):
    section_key = "shipping"
    section_title = "Shipping"

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        self.weight = _dspin(decimals=2)
        self.weight_unit = QComboBox()
        self.weight_unit.addItems(["LBS", "KG", "OZ"])
        self.length = _dspin(decimals=2)
        self.width = _dspin(decimals=2)
        self.height = _dspin(decimals=2)
        self.dim_unit = QComboBox()
        self.dim_unit.addItems(["IN", "CM"])
        self.shipping_cost = _dspin(prefix="$ ")

        grid.addWidget(_label("Weight", dim=True, small=True), 0, 0)
        grid.addWidget(self.weight, 1, 0)
        grid.addWidget(self.weight_unit, 1, 1)
        grid.addWidget(_label("Shipping cost", dim=True, small=True), 0, 2, 1, 2)
        grid.addWidget(self.shipping_cost, 1, 2, 1, 2)

        grid.addWidget(_label("L × W × H", dim=True, small=True), 2, 0, 1, 4)
        grid.addWidget(self.length, 3, 0)
        grid.addWidget(self.width, 3, 1)
        grid.addWidget(self.height, 3, 2)
        grid.addWidget(self.dim_unit, 3, 3)

        for w in (self.weight, self.length, self.width, self.height,
                  self.shipping_cost):
            w.valueChanged.connect(self.changed)
        self.weight_unit.currentIndexChanged.connect(self.changed)
        self.dim_unit.currentIndexChanged.connect(self.changed)

    def populate(self, p: dict) -> None:
        self.weight.setValue(float(p.get("weight") or 0))
        self.length.setValue(float(p.get("length") or 0))
        self.width.setValue(float(p.get("width") or 0))
        self.height.setValue(float(p.get("height") or 0))
        self.shipping_cost.setValue(float(p.get("shipping_cost") or 0))
        for combo, key, default in (
            (self.weight_unit, "weight_unit", "LBS"),
            (self.dim_unit, "dimension_unit", "IN"),
        ):
            val = str(p.get(key) or default)
            idx = combo.findText(val, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def collect(self) -> dict[str, Any]:
        return {
            "weight": self.weight.value(),
            "weight_unit": self.weight_unit.currentText(),
            "length": self.length.value(),
            "width": self.width.value(),
            "height": self.height.value(),
            "dimension_unit": self.dim_unit.currentText(),
            "shipping_cost": self.shipping_cost.value(),
        }


__all__ = [
    "BasicSection",
    "CoreChargeSection",
    "PlatformSection",
    "SupplierSection",
    "WarrantySection",
    "PricingSection",
    "InventorySection",
    "NotesSection",
    "ShippingSection",
    "CrossReferencesSection",
    "SuggestedSellsSection",
    "ImagesSection",
    "ShopifySection",
    "PricingTiersTable",
]


# ── 3. PLATFORM ──────────────────────────────────────────────────────────
class PlatformSection(_SectionBase):
    section_key = "platform"
    section_title = "Platform"

    _DEFAULT_ENGINE_MFRS = [
        "", "Caterpillar", "Cummins", "Detroit Diesel",
        "International / Navistar", "Mack", "PACCAR", "Volvo",
    ]
    _DEFAULT_TRUCK_MFRS = [
        "", "Freightliner", "Kenworth", "Peterbilt", "International",
        "Volvo", "Mack", "Western Star", "Ford", "Chevrolet/GMC",
    ]
    _DEFAULT_TRUCK_SYSTEMS = [
        "", "Engine", "Fuel System", "Cooling", "Air Intake",
        "Exhaust / Aftertreatment", "Transmission", "Drivetrain",
        "Brakes", "Electrical", "HVAC", "Suspension",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)
        form.setContentsMargins(0, 0, 0, 0)

        self.engine_mfr = QComboBox()
        self.engine_mfr.setEditable(True)
        self.engine_model = QComboBox()
        self.engine_model.setEditable(True)
        self.truck_mfr = QComboBox()
        self.truck_mfr.setEditable(True)
        self.truck_model = QLineEdit()
        self.truck_model.setPlaceholderText("e.g. Cascadia, T680")
        self.truck_system = QComboBox()
        self.truck_system.setEditable(True)

        # Populate defaults; DB lists overlay if available
        self.engine_mfr.addItems(self._DEFAULT_ENGINE_MFRS)
        if get_all_engine_manufacturers is not None:
            try:
                for m in get_all_engine_manufacturers() or []:
                    name = m if isinstance(m, str) else m.get("name") or ""
                    if name and self.engine_mfr.findText(name) < 0:
                        self.engine_mfr.addItem(name)
            except Exception:
                pass

        if get_all_engine_models is not None:
            try:
                for m in get_all_engine_models() or []:
                    name = m if isinstance(m, str) else m.get("name") or ""
                    if name:
                        self.engine_model.addItem(name)
            except Exception:
                pass

        self.truck_mfr.addItems(self._DEFAULT_TRUCK_MFRS)
        self.truck_system.addItems(self._DEFAULT_TRUCK_SYSTEMS)

        form.addRow("Engine manufacturer", self.engine_mfr)
        form.addRow("Engine model", self.engine_model)
        form.addRow("Truck manufacturer", self.truck_mfr)
        form.addRow("Truck model", self.truck_model)
        form.addRow("Truck system", self.truck_system)

        for w in (self.engine_mfr, self.engine_model,
                  self.truck_mfr, self.truck_system):
            w.editTextChanged.connect(self.changed)
        self.truck_model.textChanged.connect(self.changed)

    def populate(self, p: dict) -> None:
        self.engine_mfr.setEditText(str(p.get("oem_manufacturer") or ""))
        self.engine_model.setEditText(str(p.get("engine") or ""))
        self.truck_mfr.setEditText(str(p.get("truck_manufacturer") or ""))
        self.truck_model.setText(str(p.get("truck_model") or ""))
        self.truck_system.setEditText(str(p.get("truck_system") or ""))

    def collect(self) -> dict[str, Any]:
        return {
            "oem_manufacturer": self.engine_mfr.currentText().strip(),
            "engine": self.engine_model.currentText().strip(),
            "truck_manufacturer": self.truck_mfr.currentText().strip(),
            "truck_model": self.truck_model.text().strip(),
            "truck_system": self.truck_system.currentText().strip(),
        }


# ── 4. SUPPLIER / VENDOR ─────────────────────────────────────────────────
class SupplierSection(_SectionBase):
    section_key = "supplier"
    section_title = "Supplier / Vendor"
    is_required = True

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)
        form.setContentsMargins(0, 0, 0, 0)

        self.supplier_combo = QComboBox()
        self.supplier_combo.setEditable(True)
        self._supplier_id_by_name: dict[str, int] = {}
        self._load_vendors()

        self.pai_sku = _line_edit("Vendor part number")
        self.pai_link = _line_edit("https://… (catalog URL)")
        self.vendor_desc = _line_edit("How the vendor lists this item")
        self.vendor_avail = _line_edit("e.g. In stock, 3-5 days")
        self.vendor_alts = _line_edit("Comma-separated alternates")
        self.private_label = QCheckBox("Private-label this product")
        self.private_label_pn = _line_edit("Private-label part number")

        form.addRow("Vendor *", self.supplier_combo)
        form.addRow("Vendor SKU", self.pai_sku)
        form.addRow("Vendor link", self.pai_link)
        form.addRow("Vendor description", self.vendor_desc)
        form.addRow("Availability", self.vendor_avail)
        form.addRow("Alternates", self.vendor_alts)
        form.addRow("", self.private_label)
        form.addRow("Private-label PN", self.private_label_pn)

        self.supplier_combo.editTextChanged.connect(self.changed)
        for w in (self.pai_sku, self.pai_link, self.vendor_desc,
                  self.vendor_avail, self.vendor_alts,
                  self.private_label_pn):
            w.textChanged.connect(self.changed)
        self.private_label.toggled.connect(self.changed)
        self.private_label.toggled.connect(self.private_label_pn.setEnabled)
        self.private_label_pn.setEnabled(False)

    def _load_vendors(self) -> None:
        self.supplier_combo.addItem("", None)
        if get_all_vendors is None:
            return
        try:
            for v in get_all_vendors() or []:
                name = v.get("name") if isinstance(v, dict) else str(v)
                vid = v.get("id") if isinstance(v, dict) else None
                if not name:
                    continue
                self.supplier_combo.addItem(name, vid)
                if vid is not None:
                    self._supplier_id_by_name[name] = vid
        except Exception:
            pass

    def populate(self, p: dict) -> None:
        name = str(p.get("supplier") or "")
        self.supplier_combo.setEditText(name)
        self.pai_sku.setText(str(p.get("pai_sku") or ""))
        self.pai_link.setText(str(p.get("pai_link") or ""))
        self.vendor_desc.setText(str(p.get("vendor_description") or ""))
        self.vendor_avail.setText(str(p.get("vendor_availability") or ""))
        self.vendor_alts.setText(str(p.get("vendor_alternates") or ""))
        self.private_label.setChecked(bool(p.get("private_label")))
        self.private_label_pn.setText(str(p.get("private_label_part_number") or ""))
        self.private_label_pn.setEnabled(self.private_label.isChecked())

    def collect(self) -> dict[str, Any]:
        name = self.supplier_combo.currentText().strip()
        # Prefer the combo's userData (vendor id) for the selected entry
        vid = self.supplier_combo.currentData()
        if vid is None and name in self._supplier_id_by_name:
            vid = self._supplier_id_by_name[name]
        out: dict[str, Any] = {
            "supplier": name,
            "pai_sku": self.pai_sku.text().strip(),
            "pai_link": self.pai_link.text().strip(),
            "vendor_description": self.vendor_desc.text().strip(),
            "vendor_availability": self.vendor_avail.text().strip(),
            "vendor_alternates": self.vendor_alts.text().strip(),
            "private_label": 1 if self.private_label.isChecked() else 0,
            "private_label_part_number": (
                self.private_label_pn.text().strip()
                if self.private_label.isChecked() else ""
            ),
        }
        if vid is not None:
            out["preferred_vendor_id"] = vid
        return out

    def validate(self) -> list[str]:
        if not self.supplier_combo.currentText().strip():
            return ["Vendor / supplier is required."]
        return []


# ── 5. WARRANTY ──────────────────────────────────────────────────────────
class WarrantySection(_SectionBase):
    section_key = "warranty"
    section_title = "Warranty"

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        # --- Supplier warranty ---
        sw_header = _label("Supplier warranty", small=True, dim=True)
        v.addWidget(sw_header)
        self.sw_enabled = QCheckBox("Supplier provides a warranty")
        v.addWidget(self.sw_enabled)
        sw_row = QHBoxLayout()
        sw_row.setSpacing(8)
        self.sw_value = _spin(0, 9999)
        self.sw_unit = QComboBox()
        self.sw_unit.addItems(["days", "months", "years"])
        sw_row.addWidget(_label("Term", dim=True, small=True))
        sw_row.addWidget(self.sw_value)
        sw_row.addWidget(self.sw_unit)
        sw_row.addStretch(1)
        sw_box = QWidget()
        sw_box.setLayout(sw_row)
        v.addWidget(sw_box)
        self._sw_box = sw_box

        # --- JAKS extended warranty ---
        jw_header = _label("JAKS extended warranty", small=True, dim=True)
        v.addWidget(jw_header)
        self.jw_enabled = QCheckBox("Offer JAKS extended warranty")
        v.addWidget(self.jw_enabled)
        jw_row = QHBoxLayout()
        jw_row.setSpacing(8)
        self.jw_pct = _dspin(0.0, 100.0, decimals=1)
        self.jw_pct.setSuffix(" %")
        self.jw_charge = _dspin(prefix="$ ")
        jw_row.addWidget(_label("Percent of price", dim=True, small=True))
        jw_row.addWidget(self.jw_pct)
        jw_row.addWidget(_label("Charge", dim=True, small=True))
        jw_row.addWidget(self.jw_charge)
        jw_row.addStretch(1)
        jw_box = QWidget()
        jw_box.setLayout(jw_row)
        v.addWidget(jw_box)
        self._jw_box = jw_box

        self.sw_enabled.toggled.connect(self.changed)
        self.sw_enabled.toggled.connect(self._sw_box.setEnabled)
        self.jw_enabled.toggled.connect(self.changed)
        self.jw_enabled.toggled.connect(self._jw_box.setEnabled)
        self.sw_value.valueChanged.connect(self.changed)
        self.sw_unit.currentIndexChanged.connect(self.changed)
        self.jw_pct.valueChanged.connect(self.changed)
        self.jw_charge.valueChanged.connect(self.changed)
        self._sw_box.setEnabled(False)
        self._jw_box.setEnabled(False)

    def populate(self, p: dict) -> None:
        self.sw_enabled.setChecked(bool(p.get("supplier_warranty_enabled")))
        self.sw_value.setValue(int(p.get("supplier_warranty_value") or 0))
        unit = str(p.get("supplier_warranty_unit") or "days")
        i = self.sw_unit.findText(unit)
        if i >= 0:
            self.sw_unit.setCurrentIndex(i)
        self.jw_enabled.setChecked(bool(p.get("jaks_warranty_enabled")))
        self.jw_pct.setValue(float(p.get("warranty_percentage") or 0))
        self.jw_charge.setValue(float(p.get("jaks_warranty_charge") or 0))
        self._sw_box.setEnabled(self.sw_enabled.isChecked())
        self._jw_box.setEnabled(self.jw_enabled.isChecked())

    def collect(self) -> dict[str, Any]:
        return {
            "supplier_warranty_enabled": 1 if self.sw_enabled.isChecked() else 0,
            "supplier_warranty_value": self.sw_value.value() if self.sw_enabled.isChecked() else 0,
            "supplier_warranty_unit": self.sw_unit.currentText(),
            "jaks_warranty_enabled": 1 if self.jw_enabled.isChecked() else 0,
            "warranty_percentage": self.jw_pct.value() if self.jw_enabled.isChecked() else 0,
            "jaks_warranty_charge": self.jw_charge.value() if self.jw_enabled.isChecked() else 0,
        }


# ── 6. CROSS-REFERENCES ──────────────────────────────────────────────────
class CrossReferencesSection(_SectionBase):
    section_key = "xref"
    section_title = "Cross-References"

    _TYPES = ["OEM", "Aftermarket", "Equivalent", "Supersedes", "Superseded by"]

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        self._product_id: Optional[int] = None
        self._unsaved_warning = _label(
            "Save the product before adding cross-references.",
            dim=True, small=True,
        )
        v.addWidget(self._unsaved_warning)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Type", "Number", "Manufacturer", "Notes"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setStyleSheet(
            f"QTableWidget {{ background:{_PANEL2}; color:{_TEXT}; "
            f"gridline-color:{_BORDER}; border:1px solid {_BORDER}; }}"
            f"QHeaderView::section {{ background:{_PANEL3}; color:{_DIM}; "
            f"border:0; padding:6px; }}"
        )
        v.addWidget(self.table)

        # Add row
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.in_type = QComboBox()
        self.in_type.addItems(self._TYPES)
        self.in_number = _line_edit("Part number")
        self.in_mfr = _line_edit("Manufacturer (optional)")
        self.in_notes = _line_edit("Notes (optional)")
        self.btn_add = QPushButton("Add")
        self.btn_remove = QPushButton("Remove selected")
        add_row.addWidget(self.in_type)
        add_row.addWidget(self.in_number, 2)
        add_row.addWidget(self.in_mfr, 2)
        add_row.addWidget(self.in_notes, 2)
        add_row.addWidget(self.btn_add)
        add_row.addWidget(self.btn_remove)
        v.addLayout(add_row)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_remove.clicked.connect(self._on_remove)

    def populate(self, p: dict) -> None:
        self._product_id = p.get("id")
        self._unsaved_warning.setVisible(not self._product_id)
        self._reload()

    def _reload(self) -> None:
        self.table.setRowCount(0)
        if not self._product_id or get_interchanges is None:
            return
        try:
            rows = get_interchanges(self._product_id) or []
        except Exception:
            rows = []
        for r in rows:
            self._append_row(
                r.get("id"),
                r.get("interchange_type", ""),
                r.get("interchange_number", ""),
                r.get("manufacturer", "") or "",
                r.get("notes", "") or "",
            )

    def _append_row(self, row_id, t, num, mfr, notes) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, val in enumerate([t, num, mfr, notes]):
            item = QTableWidgetItem(str(val))
            if c == 0 and row_id is not None:
                item.setData(Qt.ItemDataRole.UserRole, int(row_id))
            self.table.setItem(r, c, item)

    def _on_add(self) -> None:
        num = self.in_number.text().strip()
        if not num:
            return
        if not self._product_id or add_interchange is None:
            return
        try:
            add_interchange(
                self._product_id,
                num,
                interchange_type=self.in_type.currentText(),
                manufacturer=self.in_mfr.text().strip() or None,
                notes=self.in_notes.text().strip() or None,
            )
        except Exception:
            return
        self.in_number.clear()
        self.in_mfr.clear()
        self.in_notes.clear()
        self._reload()
        self.changed.emit()

    def _on_remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows or delete_interchange is None:
            return
        for r in rows:
            item = self.table.item(r, 0)
            if item is None:
                continue
            rid = item.data(Qt.ItemDataRole.UserRole)
            if rid is None:
                continue
            try:
                delete_interchange(int(rid))
            except Exception:
                pass
        self._reload()
        self.changed.emit()


# ── 7. SUGGESTED SELLS ───────────────────────────────────────────────────
class SuggestedSellsSection(_SectionBase):
    section_key = "suggested"
    section_title = "Suggested Sells"

    _RELS = ["recommended", "required", "upsell", "cross-sell"]

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        self._product_id: Optional[int] = None
        self._unsaved_warning = _label(
            "Save the product before adding suggested sells.",
            dim=True, small=True,
        )
        v.addWidget(self._unsaved_warning)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["SKU", "Title", "Type", "Notes"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setStyleSheet(
            f"QTableWidget {{ background:{_PANEL2}; color:{_TEXT}; "
            f"gridline-color:{_BORDER}; border:1px solid {_BORDER}; }}"
            f"QHeaderView::section {{ background:{_PANEL3}; color:{_DIM}; "
            f"border:0; padding:6px; }}"
        )
        v.addWidget(self.table)

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.in_sku = _line_edit("SKU to link")
        self.in_rel = QComboBox()
        self.in_rel.addItems(self._RELS)
        self.in_notes = _line_edit("Notes (optional)")
        self.btn_add = QPushButton("Add")
        self.btn_remove = QPushButton("Remove selected")
        add_row.addWidget(self.in_sku, 2)
        add_row.addWidget(self.in_rel)
        add_row.addWidget(self.in_notes, 2)
        add_row.addWidget(self.btn_add)
        add_row.addWidget(self.btn_remove)
        v.addLayout(add_row)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_remove.clicked.connect(self._on_remove)

    def populate(self, p: dict) -> None:
        self._product_id = p.get("id")
        self._unsaved_warning.setVisible(not self._product_id)
        self._reload()

    def _reload(self) -> None:
        self.table.setRowCount(0)
        if not self._product_id or get_suggested_sells is None:
            return
        try:
            rows = get_suggested_sells(self._product_id) or []
        except Exception:
            rows = []
        for r in rows:
            self._append_row(
                r.get("id"),
                r.get("sku", ""),
                r.get("title", ""),
                r.get("relationship_type", ""),
                r.get("notes", "") or "",
            )

    def _append_row(self, row_id, sku, title, rel, notes) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, val in enumerate([sku, title, rel, notes]):
            item = QTableWidgetItem(str(val))
            if c == 0 and row_id is not None:
                item.setData(Qt.ItemDataRole.UserRole, int(row_id))
            self.table.setItem(r, c, item)

    def _resolve_sku(self, sku: str) -> Optional[int]:
        """Look up a product id by SKU using available DB helpers."""
        try:
            from db import get_product_by_sku as _gp
            row = _gp(sku)
            if row:
                return row.get("id")
        except Exception:
            pass
        return None

    def _on_add(self) -> None:
        sku = self.in_sku.text().strip()
        if not sku or not self._product_id or add_suggested_sell is None:
            return
        sid = self._resolve_sku(sku)
        if sid is None:
            return
        try:
            add_suggested_sell(
                self._product_id,
                sid,
                relationship_type=self.in_rel.currentText(),
                notes=self.in_notes.text().strip(),
            )
        except Exception:
            return
        self.in_sku.clear()
        self.in_notes.clear()
        self._reload()
        self.changed.emit()

    def _on_remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows or delete_suggested_sell is None:
            return
        for r in rows:
            item = self.table.item(r, 0)
            if item is None:
                continue
            rid = item.data(Qt.ItemDataRole.UserRole)
            if rid is None:
                continue
            try:
                delete_suggested_sell(int(rid))
            except Exception:
                pass
        self._reload()
        self.changed.emit()


# ── 8. IMAGES ────────────────────────────────────────────────────────────
class ImagesSection(_SectionBase):
    section_key = "images"
    section_title = "Images"

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem, QFileDialog
        from PySide6.QtGui import QPixmap, QIcon

        self._QFileDialog = QFileDialog
        self._QPixmap = QPixmap
        self._QIcon = QIcon
        self._QListWidgetItem = QListWidgetItem

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        self._product_id: Optional[int] = None
        self._unsaved_warning = _label(
            "Save the product before adding images.",
            dim=True, small=True,
        )
        v.addWidget(self._unsaved_warning)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(self.list.iconSize().__class__(96, 96) if hasattr(self.list.iconSize(), "__class__") else self.list.iconSize())
        from PySide6.QtCore import QSize
        self.list.setIconSize(QSize(96, 96))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setSpacing(6)
        self.list.setStyleSheet(
            f"QListWidget {{ background:{_PANEL2}; color:{_TEXT}; "
            f"border:1px solid {_BORDER}; }}"
        )
        v.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_add = QPushButton("Import image…")
        self.btn_remove = QPushButton("Remove selected")
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_remove)
        v.addLayout(btn_row)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_remove.clicked.connect(self._on_remove)

    def populate(self, p: dict) -> None:
        self._product_id = p.get("id")
        self._unsaved_warning.setVisible(not self._product_id)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        if not self._product_id or get_product_images is None:
            return
        try:
            rows = get_product_images(self._product_id) or []
        except Exception:
            rows = []
        for r in rows:
            path = r.get("path") or ""
            name = r.get("filename") or path
            item = self._QListWidgetItem(name)
            pix = self._QPixmap(path)
            if not pix.isNull():
                item.setIcon(self._QIcon(pix))
            item.setData(Qt.ItemDataRole.UserRole, int(r.get("id")))
            self.list.addItem(item)

    def _on_add(self) -> None:
        if not self._product_id or add_product_image is None:
            return
        files, _ = self._QFileDialog.getOpenFileNames(
            self, "Import images", "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp)",
        )
        if not files:
            return
        import os
        for path in files:
            try:
                add_product_image(
                    self._product_id,
                    os.path.basename(path),
                    path,
                    position=self.list.count() + 1,
                    source="upload",
                )
            except Exception:
                pass
        self._reload()
        self.changed.emit()

    def _on_remove(self) -> None:
        items = self.list.selectedItems()
        if not items or delete_product_image is None:
            return
        for it in items:
            rid = it.data(Qt.ItemDataRole.UserRole)
            if rid is None:
                continue
            try:
                delete_product_image(int(rid))
            except Exception:
                pass
        self._reload()
        self.changed.emit()


# ── 9. SHOPIFY / SEO ─────────────────────────────────────────────────────
class ShopifySection(_SectionBase):
    section_key = "shopify"
    section_title = "Shopify / SEO"

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)
        form.setContentsMargins(0, 0, 0, 0)

        self.shopify_handle = _line_edit("auto-generated from title if blank")
        self.shopify_status = QComboBox()
        self.shopify_status.addItems(["draft", "active", "archived"])
        self.tags = _line_edit("comma-separated tags")
        self.seo_title = _line_edit("Custom SEO title (optional)")
        self.seo_description = QPlainTextEdit()
        self.seo_description.setPlaceholderText("Meta description (optional)")
        self.seo_description.setFixedHeight(72)
        self.publish_shopify = QCheckBox("Publish to Shopify")

        form.addRow("Shopify handle", self.shopify_handle)
        form.addRow("Status", self.shopify_status)
        form.addRow("Tags", self.tags)
        form.addRow("SEO title", self.seo_title)
        form.addRow("SEO description", self.seo_description)
        form.addRow("", self.publish_shopify)

        for w in (self.shopify_handle, self.tags, self.seo_title):
            w.textChanged.connect(self.changed)
        self.shopify_status.currentIndexChanged.connect(self.changed)
        self.seo_description.textChanged.connect(self.changed)
        self.publish_shopify.toggled.connect(self.changed)

    def populate(self, p: dict) -> None:
        self.shopify_handle.setText(str(p.get("handle") or ""))
        status = str(p.get("shopify_status") or "draft")
        i = self.shopify_status.findText(status)
        if i >= 0:
            self.shopify_status.setCurrentIndex(i)
        self.tags.setText(str(p.get("tags") or ""))
        self.seo_title.setText(str(p.get("seo_title") or ""))
        self.seo_description.setPlainText(str(p.get("seo_description") or ""))
        self.publish_shopify.setChecked(bool(p.get("publish_shopify")))

    def collect(self) -> dict[str, Any]:
        return {
            "handle": self.shopify_handle.text().strip(),
            "shopify_status": self.shopify_status.currentText(),
            "tags": self.tags.text().strip(),
            "seo_title": self.seo_title.text().strip(),
            "seo_description": self.seo_description.toPlainText().strip(),
            "publish_shopify": 1 if self.publish_shopify.isChecked() else 0,
        }
