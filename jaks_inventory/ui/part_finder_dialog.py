"""Part Finder — lightweight standalone lookup dialog.

Searches for a part number across internal inventory, internal cross
references, and vendor sources. Returns grouped results with action buttons.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QGridLayout,
    QFrame,
    QRadioButton,
    QButtonGroup,
    QApplication,
)


class PartFinderDialog(QDialog):
    """Part Finder dialog — search by OEM, vendor, xref, or keyword.

    Signals:
      add_to_quote_requested(dict) — user wants to add a product to a quote.
      open_product_requested(int)  — user wants to open a product detail view.
      import_requested(dict)       — user wants to import a vendor result.
    """

    add_to_quote_requested = Signal(dict)
    open_product_requested = Signal(int)
    import_requested = Signal(dict)

    def __init__(self, *, initial_query: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔍 Part Finder")
        self.setMinimumSize(900, 600)
        self.resize(1000, 700)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)

        self._results_widgets: list[QWidget] = []
        self._build_ui()

        if initial_query:
            self._search_edit.setText(initial_query)
            self._do_search()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # ── Title bar ──
        title = QLabel("🔍 Part Finder")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1F2937;")
        root.addWidget(title)

        # ── Search bar row ──
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Enter part number, OEM, vendor PN, cross-ref, or keyword…")
        self._search_edit.setStyleSheet(
            "QLineEdit { padding: 10px 14px; border: 2px solid #6B8E23; "
            "border-radius: 8px; font-size: 14px; }"
            "QLineEdit:focus { border-color: #556B2F; background: #FAFFF5; }"
        )
        self._search_edit.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_edit, 1)

        self._search_btn = QPushButton("Search")
        self._search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_btn.setStyleSheet(
            "QPushButton { background: #6B8E23; color: white; font-weight: 600; "
            "padding: 10px 24px; border-radius: 8px; font-size: 14px; }"
            "QPushButton:hover { background: #556B2F; }"
        )
        self._search_btn.clicked.connect(self._do_search)
        search_row.addWidget(self._search_btn)

        root.addLayout(search_row)

        # ── Search type filters ──
        filter_row = QHBoxLayout()
        filter_row.setSpacing(12)
        filter_row.addWidget(QLabel("Search:"))

        self._filter_group = QButtonGroup(self)
        for idx, (label, tip) in enumerate([
            ("All Sources", "Search everywhere"),
            ("Internal Only", "Inventory + cross-refs only"),
            ("Vendors Only", "PAI + SAMPA only"),
        ]):
            rb = QRadioButton(label)
            rb.setToolTip(tip)
            self._filter_group.addButton(rb, idx)
            filter_row.addWidget(rb)
        self._filter_group.button(0).setChecked(True)

        filter_row.addStretch()

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #6B7280; font-size: 12px;")
        filter_row.addWidget(self._status_label)

        root.addLayout(filter_row)

        # ── Results scroll area ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: 1px solid #E5E7EB; border-radius: 6px; background: #FAFAFA; }"
        )

        self._results_container = QWidget()
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setContentsMargins(8, 8, 8, 8)
        self._results_layout.setSpacing(12)
        self._results_layout.addStretch()

        scroll.setWidget(self._results_container)
        root.addWidget(scroll, 1)

        # ── Bottom bar ──
        bottom = QHBoxLayout()
        bottom.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            "QPushButton { padding: 8px 20px; border: 1px solid #D1D5DB; "
            "border-radius: 6px; font-size: 13px; }"
            "QPushButton:hover { background: #F3F4F6; }"
        )
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)

        root.addLayout(bottom)

        # Keyboard shortcut
        QShortcut(QKeySequence("Escape"), self, self.close)

    # ------------------------------------------------------------------
    # Search execution
    # ------------------------------------------------------------------
    def _do_search(self):
        query = self._search_edit.text().strip()
        if not query:
            return

        self._status_label.setText("Searching…")
        QApplication.processEvents()

        # Determine which sources to search
        filter_id = self._filter_group.checkedId()
        search_internal = filter_id in (0, 1)
        search_xref = filter_id in (0, 1)
        search_pai = filter_id in (0, 2)
        search_sampa = filter_id in (0, 2)

        try:
            from jaks_inventory.services.part_search_service import (
                PartSearchService,
                SearchSource,
            )

            svc = PartSearchService()
            grouped = svc.search(
                query,
                search_internal=search_internal,
                search_xref=search_xref,
                search_pai=search_pai,
                search_sampa=search_sampa,
            )
        except Exception as exc:
            self._status_label.setText(f"Error: {exc}")
            return

        self._clear_results()

        total = sum(len(v) for v in grouped.values())
        self._status_label.setText(f"{total} result{'s' if total != 1 else ''} found")

        if not grouped:
            no_results = QLabel(
                "No results found. Try a different part number or keyword."
            )
            no_results.setStyleSheet(
                "color: #9CA3AF; font-size: 14px; padding: 40px; "
            )
            no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._results_layout.insertWidget(0, no_results)
            self._results_widgets.append(no_results)
            return

        # Display order
        from jaks_inventory.services.part_search_service import SearchSource

        source_order = [
            SearchSource.INTERNAL,
            SearchSource.XREF,
            SearchSource.PAI,
            SearchSource.SAMPA,
        ]

        insert_idx = 0
        for source in source_order:
            results = grouped.get(source, [])
            if not results:
                continue

            group = self._build_source_group(source, results)
            self._results_layout.insertWidget(insert_idx, group)
            self._results_widgets.append(group)
            insert_idx += 1

    def _clear_results(self):
        for w in self._results_widgets:
            self._results_layout.removeWidget(w)
            w.deleteLater()
        self._results_widgets.clear()

    # ------------------------------------------------------------------
    # Result group builders
    # ------------------------------------------------------------------
    def _build_source_group(self, source, results) -> QGroupBox:
        """Build a collapsible group box for a search source."""
        from jaks_inventory.services.part_search_service import SearchSource

        icons = {
            SearchSource.INTERNAL: "📦",
            SearchSource.XREF: "🔗",
            SearchSource.PAI: "🏭",
            SearchSource.SAMPA: "🚛",
        }
        colors = {
            SearchSource.INTERNAL: "#065F46",
            SearchSource.XREF: "#1E40AF",
            SearchSource.PAI: "#92400E",
            SearchSource.SAMPA: "#7C3AED",
        }
        bg_colors = {
            SearchSource.INTERNAL: "#F0FDF4",
            SearchSource.XREF: "#EFF6FF",
            SearchSource.PAI: "#FFFBEB",
            SearchSource.SAMPA: "#F5F3FF",
        }

        icon = icons.get(source, "📋")
        color = colors.get(source, "#374151")
        bg = bg_colors.get(source, "#F9FAFB")

        group = QGroupBox(f"{icon}  {source.value}  ({len(results)})")
        group.setStyleSheet(
            f"QGroupBox {{ font-size: 14px; font-weight: bold; color: {color}; "
            f"border: 1px solid #E5E7EB; border-radius: 8px; padding-top: 20px; "
            f"margin-top: 6px; background: {bg}; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 12px; "
            f"padding: 0 6px; }}"
        )

        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        for result in results:
            card = self._build_result_card(result, source)
            layout.addWidget(card)

        return group

    def _build_result_card(self, result, source) -> QFrame:
        """Build a single result row with info and action buttons."""
        from jaks_inventory.services.part_search_service import SearchSource

        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; "
            "border-radius: 6px; padding: 8px; }"
            "QFrame:hover { border-color: #6B8E23; }"
        )

        row = QHBoxLayout(card)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(12)

        # Left info section
        info = QVBoxLayout()
        info.setSpacing(2)

        # Title line
        title_text = result.sku
        if result.title:
            title_text = f"{result.sku}  —  {result.title}"
        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet("font-weight: 600; font-size: 13px; color: #1F2937;")
        title_lbl.setWordWrap(True)
        info.addWidget(title_lbl)

        # Detail line
        details = []
        if result.manufacturer:
            details.append(f"Mfr: {result.manufacturer}")
        if result.vendor:
            details.append(f"Vendor: {result.vendor}")
        if result.oem_part_number:
            details.append(f"OEM: {result.oem_part_number}")
        if result.vendor_part_number:
            details.append(f"VPN: {result.vendor_part_number}")
        if result.matched_number:
            details.append(f"Matched: {result.matched_number}")
        if result.category:
            details.append(f"Cat: {result.category}")

        if details:
            detail_lbl = QLabel("  ·  ".join(details))
            detail_lbl.setStyleSheet("color: #6B7280; font-size: 11px;")
            detail_lbl.setWordWrap(True)
            info.addWidget(detail_lbl)

        row.addLayout(info, 1)

        # Middle — stock + pricing
        mid = QVBoxLayout()
        mid.setSpacing(1)
        mid.setAlignment(Qt.AlignmentFlag.AlignRight)

        if result.is_internal:
            qty_lbl = QLabel(f"QOH: {result.qty_on_hand}")
            qty_color = "#065F46" if result.qty_on_hand > 0 else "#DC2626"
            qty_lbl.setStyleSheet(f"font-weight: 600; color: {qty_color}; font-size: 12px;")
            mid.addWidget(qty_lbl)

        if result.price:
            price_lbl = QLabel(f"${result.price:,.2f}")
            price_lbl.setStyleSheet("font-weight: 600; color: #1F2937; font-size: 12px;")
            mid.addWidget(price_lbl)

        if result.cost:
            cost_lbl = QLabel(f"Cost: ${result.cost:,.2f}")
            cost_lbl.setStyleSheet("color: #6B7280; font-size: 11px;")
            mid.addWidget(cost_lbl)

        row.addLayout(mid)

        # Right — action buttons
        actions = QVBoxLayout()
        actions.setSpacing(4)

        if result.is_internal and result.product_id:
            add_btn = self._action_btn("➕ Add to Quote", "#6B8E23")
            add_btn.clicked.connect(
                lambda _, r=result: self._on_add_to_quote(r)
            )
            actions.addWidget(add_btn)

            open_btn = self._action_btn("📝 Open Product", "#374151")
            open_btn.clicked.connect(
                lambda _, pid=result.product_id: self.open_product_requested.emit(pid)
            )
            actions.addWidget(open_btn)
        else:
            # Vendor result — offer import
            import_btn = self._action_btn("📥 Import to Inventory", "#1E40AF")
            import_btn.clicked.connect(
                lambda _, r=result: self._on_import(r)
            )
            actions.addWidget(import_btn)

        row.addLayout(actions)

        return card

    @staticmethod
    def _action_btn(text: str, color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(28)
        btn.setStyleSheet(
            f"QPushButton {{ background: {color}; color: white; font-size: 11px; "
            f"font-weight: 600; border: none; border-radius: 4px; padding: 0 12px; }}"
            f"QPushButton:hover {{ opacity: 0.85; }}"
        )
        return btn

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_add_to_quote(self, result):
        """Emit signal to add this product to the active quote."""
        self.add_to_quote_requested.emit({
            "product_id": result.product_id,
            "sku": result.sku,
            "title": result.title,
            "price": result.price,
            "cost": result.cost,
            "qty_on_hand": result.qty_on_hand,
            "supplier": result.vendor,
            "manufacturer": result.manufacturer,
        })

    def _on_import(self, result):
        """Open vendor import dialog for this result."""
        self.import_requested.emit({
            "vendor": result.vendor or result.source.value,
            "vendor_part_number": result.vendor_part_number,
            "oem_part_number": result.oem_part_number,
            "title": result.title,
            "description": result.description,
            "cost": result.cost,
            "price": result.price,
            "manufacturer": result.manufacturer,
            "category": result.category,
            "raw": result.raw,
        })
