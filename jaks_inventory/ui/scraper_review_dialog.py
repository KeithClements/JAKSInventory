"""Scraper Review Dialog — two-tab dialog to accept/reject scraped data.

Tab 1: Cross-References  (from scraped_cross_references)
Tab 2: Competitor Prices  (from competitor_prices)

Each row has Accept / Reject buttons + a checkbox for batch operations.
Accepted xrefs are written to the live ``part_interchanges`` table.
Accepted prices are kept as reference.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from db import (
    get_pending_scraped_xrefs,
    accept_scraped_xref,
    reject_scraped_xref,
    get_pending_competitor_prices,
    accept_competitor_price,
    reject_competitor_price,
    get_scraped_xref_counts,
    get_competitor_price_counts,
)

# Shared styles
_BTN_ACCEPT = (
    "QPushButton { background-color: #4a6741; color: white; font-weight: bold; "
    "padding: 3px 10px; border-radius: 3px; font-size: 9pt; }"
    "QPushButton:hover { background-color: #5a7a51; }"
)
_BTN_REJECT = (
    "QPushButton { background-color: #c62828; color: white; font-weight: bold; "
    "padding: 3px 10px; border-radius: 3px; font-size: 9pt; }"
    "QPushButton:hover { background-color: #d32f2f; }"
)
_BTN_BATCH = (
    "QPushButton { padding: 4px 14px; border: 1px solid #aaa; "
    "border-radius: 4px; font-weight: bold; font-size: 9pt; }"
    "QPushButton:hover { background-color: #ececec; }"
    "QPushButton:disabled { color: #bbb; }"
)
_ROW_SELECTED_BG = QColor("#e8f0e6")  # light green tint, not overpowering


class ScraperReviewDialog(QDialog):
    """Two-tab dialog for reviewing scraped cross-references and prices."""

    def __init__(self, product_id: int | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._product_id = product_id
        self.setWindowTitle("Review Scraped Data")
        self.setMinimumSize(900, 550)
        self.resize(1050, 620)
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._tabs = QTabWidget()

        # --- Xref tab ---
        xref_page = QWidget()
        xref_lay = QVBoxLayout(xref_page)

        # Summary bar
        self._xref_summary = QLabel()
        self._xref_summary.setStyleSheet(
            "font-size: 11pt; padding: 4px 8px; background: #f5f5f5; "
            "border: 1px solid #ddd; border-radius: 4px;"
        )
        xref_lay.addWidget(self._xref_summary)

        # Table: checkbox, ID, Source PN, Found Alternate, Source, Confidence, Type, Actions
        self._xref_table = QTableWidget(0, 8)
        self._xref_table.setHorizontalHeaderLabels([
            "", "ID", "Source PN", "Found Alternate", "Source",
            "Confidence", "Type", "Actions",
        ])
        self._xref_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._xref_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._xref_table.setAlternatingRowColors(True)
        self._xref_table.verticalHeader().setDefaultSectionSize(30)
        self._xref_table.setSortingEnabled(True)
        # Resizable columns — user can drag dividers
        hdr = self._xref_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setStretchLastSection(False)
        self._xref_table.setColumnWidth(0, 30)   # checkbox
        self._xref_table.setColumnWidth(1, 45)   # ID
        self._xref_table.setColumnWidth(2, 120)  # Source PN
        self._xref_table.setColumnWidth(4, 80)   # Source
        self._xref_table.setColumnWidth(5, 85)   # Confidence
        self._xref_table.setColumnWidth(6, 100)  # Type
        self._xref_table.setColumnWidth(7, 160)  # Actions
        self._xref_table.setStyleSheet(
            "QTableWidget { gridline-color: #e0e0e0; font-size: 10pt; }"
            "QTableWidget::item:alternate { background-color: #fafafa; }"
            "QTableWidget::item:selected { background-color: #e8f0e6; color: black; }"
        )
        xref_lay.addWidget(self._xref_table, 1)

        # Batch action bar
        xref_btns = QHBoxLayout()
        self._xref_select_all_cb = QCheckBox("Select All")
        self._xref_select_all_cb.stateChanged.connect(self._toggle_all_xref_checks)
        xref_btns.addWidget(self._xref_select_all_cb)
        xref_btns.addSpacing(16)

        self._accept_sel_xref_btn = QPushButton("Accept Selected")
        self._accept_sel_xref_btn.setStyleSheet(_BTN_ACCEPT)
        self._accept_sel_xref_btn.clicked.connect(self._accept_selected_xrefs)
        self._reject_sel_xref_btn = QPushButton("Reject Selected")
        self._reject_sel_xref_btn.setStyleSheet(_BTN_REJECT)
        self._reject_sel_xref_btn.clicked.connect(self._reject_selected_xrefs)
        xref_btns.addWidget(self._accept_sel_xref_btn)
        xref_btns.addWidget(self._reject_sel_xref_btn)

        xref_btns.addStretch()

        self._accept_all_xref_btn = QPushButton("Accept All")
        self._accept_all_xref_btn.setStyleSheet(_BTN_BATCH)
        self._accept_all_xref_btn.clicked.connect(self._accept_all_xrefs)
        self._reject_all_xref_btn = QPushButton("Reject All")
        self._reject_all_xref_btn.setStyleSheet(_BTN_BATCH)
        self._reject_all_xref_btn.clicked.connect(self._reject_all_xrefs)
        xref_btns.addWidget(self._accept_all_xref_btn)
        xref_btns.addWidget(self._reject_all_xref_btn)
        xref_lay.addLayout(xref_btns)

        self._tabs.addTab(xref_page, "Cross-References")

        # --- Pricing tab ---
        price_page = QWidget()
        price_lay = QVBoxLayout(price_page)

        self._price_summary = QLabel()
        self._price_summary.setStyleSheet(
            "font-size: 11pt; padding: 4px 8px; background: #f5f5f5; "
            "border: 1px solid #ddd; border-radius: 4px;"
        )
        price_lay.addWidget(self._price_summary)

        self._price_table = QTableWidget(0, 9)
        self._price_table.setHorizontalHeaderLabels([
            "", "ID", "SKU Searched", "Competitor", "Their PN", "Price",
            "Availability", "URL", "Actions",
        ])
        self._price_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._price_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._price_table.setAlternatingRowColors(True)
        self._price_table.verticalHeader().setDefaultSectionSize(30)
        self._price_table.setSortingEnabled(True)
        phdr = self._price_table.horizontalHeader()
        phdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        phdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)  # URL stretches
        phdr.setStretchLastSection(False)
        self._price_table.setColumnWidth(0, 30)
        self._price_table.setColumnWidth(1, 45)
        self._price_table.setColumnWidth(2, 120)
        self._price_table.setColumnWidth(3, 130)
        self._price_table.setColumnWidth(4, 120)
        self._price_table.setColumnWidth(5, 80)
        self._price_table.setColumnWidth(6, 90)
        self._price_table.setColumnWidth(8, 160)
        self._price_table.setStyleSheet(
            "QTableWidget { gridline-color: #e0e0e0; font-size: 10pt; }"
            "QTableWidget::item:alternate { background-color: #fafafa; }"
            "QTableWidget::item:selected { background-color: #e8f0e6; color: black; }"
        )
        self._price_table.cellDoubleClicked.connect(self._on_price_cell_dblclick)
        self._price_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._price_table.customContextMenuRequested.connect(self._on_price_context_menu)
        price_lay.addWidget(self._price_table, 1)

        price_btns = QHBoxLayout()
        self._price_select_all_cb = QCheckBox("Select All")
        self._price_select_all_cb.stateChanged.connect(self._toggle_all_price_checks)
        price_btns.addWidget(self._price_select_all_cb)
        price_btns.addSpacing(16)

        self._accept_sel_price_btn = QPushButton("Accept Selected")
        self._accept_sel_price_btn.setStyleSheet(_BTN_ACCEPT)
        self._accept_sel_price_btn.clicked.connect(self._accept_selected_prices)
        self._reject_sel_price_btn = QPushButton("Reject Selected")
        self._reject_sel_price_btn.setStyleSheet(_BTN_REJECT)
        self._reject_sel_price_btn.clicked.connect(self._reject_selected_prices)
        price_btns.addWidget(self._accept_sel_price_btn)
        price_btns.addWidget(self._reject_sel_price_btn)

        price_btns.addStretch()

        self._accept_all_price_btn = QPushButton("Accept All")
        self._accept_all_price_btn.setStyleSheet(_BTN_BATCH)
        self._accept_all_price_btn.clicked.connect(self._accept_all_prices)
        self._reject_all_price_btn = QPushButton("Reject All")
        self._reject_all_price_btn.setStyleSheet(_BTN_BATCH)
        self._reject_all_price_btn.clicked.connect(self._reject_all_prices)
        price_btns.addWidget(self._accept_all_price_btn)
        price_btns.addWidget(self._reject_all_price_btn)
        price_lay.addLayout(price_btns)

        self._tabs.addTab(price_page, "Competitor Prices")

        # --- Main layout ---
        lay = QVBoxLayout(self)
        lay.addWidget(self._tabs, 1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bot = QHBoxLayout()
        bot.addStretch()
        bot.addWidget(close_btn)
        lay.addLayout(bot)

    # ------------------------------------------------------------------
    # Refresh / Load
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self._load_xrefs()
        self._load_prices()

    def _load_xrefs(self) -> None:
        rows = get_pending_scraped_xrefs(self._product_id)
        counts = get_scraped_xref_counts()
        pending = counts.get("pending", 0)
        accepted = counts.get("accepted", 0)
        rejected = counts.get("rejected", 0)
        self._xref_summary.setText(
            f"<b>Pending:</b> {pending} &nbsp;|&nbsp; "
            f"<b>Accepted:</b> {accepted} &nbsp;|&nbsp; "
            f"<b>Rejected:</b> {rejected}"
        )
        self._xref_table.setSortingEnabled(False)
        self._xref_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            rid = r["id"]

            # Checkbox item (sortable)
            cb_item = QTableWidgetItem()
            cb_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            cb_item.setCheckState(Qt.CheckState.Unchecked)
            cb_item.setData(Qt.ItemDataRole.UserRole, rid)
            self._xref_table.setItem(i, 0, cb_item)

            self._xref_table.setItem(i, 1, _ro_item(str(rid)))
            self._xref_table.setItem(i, 2, _ro_item(r.get("source_part_number", "")))
            self._xref_table.setItem(i, 3, _ro_item(r.get("found_alternate_number", "")))
            self._xref_table.setItem(i, 4, _ro_item(r.get("source", "")))
            self._xref_table.setItem(i, 5, _ro_item(r.get("confidence", "")))
            self._xref_table.setItem(i, 6, _ro_item(r.get("match_type", "")))
            self._xref_table.setCellWidget(i, 7, self._action_buttons(
                lambda _=None, xid=rid: self._accept_xref(xid),
                lambda _=None, xid=rid: self._reject_xref(xid),
            ))
        self._xref_table.setSortingEnabled(True)
        self._xref_table.itemChanged.connect(self._update_xref_selection_count)
        self._xref_select_all_cb.blockSignals(True)
        self._xref_select_all_cb.setChecked(False)
        self._xref_select_all_cb.blockSignals(False)
        self._update_xref_selection_count()

    def _load_prices(self) -> None:
        rows = get_pending_competitor_prices(self._product_id)
        counts = get_competitor_price_counts()
        pending = counts.get("pending", 0)
        accepted = counts.get("accepted", 0)
        rejected = counts.get("rejected", 0)
        self._price_summary.setText(
            f"<b>Pending:</b> {pending} &nbsp;|&nbsp; "
            f"<b>Accepted:</b> {accepted} &nbsp;|&nbsp; "
            f"<b>Rejected:</b> {rejected}"
        )
        self._price_table.setSortingEnabled(False)
        self._price_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            rid = r["id"]

            # Checkbox item (sortable)
            cb_item = QTableWidgetItem()
            cb_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            cb_item.setCheckState(Qt.CheckState.Unchecked)
            cb_item.setData(Qt.ItemDataRole.UserRole, rid)
            self._price_table.setItem(i, 0, cb_item)

            self._price_table.setItem(i, 1, _ro_item(str(rid)))
            self._price_table.setItem(i, 2, _ro_item(r.get("sku_searched", "")))
            self._price_table.setItem(i, 3, _ro_item(r.get("competitor_name", "")))
            self._price_table.setItem(i, 4, _ro_item(r.get("competitor_part_number", "")))
            price = r.get("competitor_price")
            self._price_table.setItem(i, 5, _ro_item(f"${price:,.2f}" if price else ""))
            self._price_table.setItem(i, 6, _ro_item(r.get("availability", "")))
            # URL — show "View Site" but store full URL in UserRole
            url_text = r.get("source_url", "") or ""
            url_item = _ro_item("View Site" if url_text else "")
            url_item.setData(Qt.ItemDataRole.UserRole, url_text)
            url_item.setToolTip(url_text)
            if url_text:
                url_item.setForeground(QColor("#1565c0"))
            self._price_table.setItem(i, 7, url_item)
            self._price_table.setCellWidget(i, 8, self._action_buttons(
                lambda _=None, pid=rid: self._accept_price(pid),
                lambda _=None, pid=rid: self._reject_price(pid),
            ))
        self._price_table.setSortingEnabled(True)
        self._price_table.itemChanged.connect(self._update_price_selection_count)
        self._price_select_all_cb.blockSignals(True)
        self._price_select_all_cb.setChecked(False)
        self._price_select_all_cb.blockSignals(False)
        self._update_price_selection_count()

    # ------------------------------------------------------------------
    # URL open on double-click / right-click copy
    # ------------------------------------------------------------------
    def _on_price_cell_dblclick(self, row: int, col: int) -> None:
        if col == 7:  # URL column
            item = self._price_table.item(row, col)
            if item:
                url = item.data(Qt.ItemDataRole.UserRole) or ""
                if url.strip():
                    QDesktopServices.openUrl(QUrl(url.strip()))

    def _on_price_context_menu(self, pos) -> None:
        """Right-click on price table — offer Copy URL if on URL column."""
        item = self._price_table.itemAt(pos)
        if not item:
            return
        col = item.column()
        if col == 7:
            url = item.data(Qt.ItemDataRole.UserRole) or ""
            if url.strip():
                menu = QMenu(self)
                copy_act = menu.addAction("📋 Copy URL")
                open_act = menu.addAction("🌐 Open in Browser")
                action = menu.exec(self._price_table.viewport().mapToGlobal(pos))
                if action == copy_act:
                    QApplication.clipboard().setText(url.strip())
                elif action == open_act:
                    QDesktopServices.openUrl(QUrl(url.strip()))

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _checked_xref_ids(self) -> list[int]:
        ids = []
        for row in range(self._xref_table.rowCount()):
            item = self._xref_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def _checked_price_ids(self) -> list[int]:
        ids = []
        for row in range(self._price_table.rowCount()):
            item = self._price_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def _toggle_all_xref_checks(self, state: int) -> None:
        checked = Qt.CheckState.Checked if state == 2 else Qt.CheckState.Unchecked
        self._xref_table.blockSignals(True)
        for row in range(self._xref_table.rowCount()):
            item = self._xref_table.item(row, 0)
            if item:
                item.setCheckState(checked)
        self._xref_table.blockSignals(False)
        self._update_xref_selection_count()

    def _toggle_all_price_checks(self, state: int) -> None:
        checked = Qt.CheckState.Checked if state == 2 else Qt.CheckState.Unchecked
        self._price_table.blockSignals(True)
        for row in range(self._price_table.rowCount()):
            item = self._price_table.item(row, 0)
            if item:
                item.setCheckState(checked)
        self._price_table.blockSignals(False)
        self._update_price_selection_count()

    def _update_xref_selection_count(self, _item=None) -> None:
        n = len(self._checked_xref_ids())
        self._accept_sel_xref_btn.setText(f"Accept Selected ({n})" if n else "Accept Selected")
        self._reject_sel_xref_btn.setText(f"Reject Selected ({n})" if n else "Reject Selected")
        self._accept_sel_xref_btn.setEnabled(n > 0)
        self._reject_sel_xref_btn.setEnabled(n > 0)

    def _update_price_selection_count(self, _item=None) -> None:
        n = len(self._checked_price_ids())
        self._accept_sel_price_btn.setText(f"Accept Selected ({n})" if n else "Accept Selected")
        self._reject_sel_price_btn.setText(f"Reject Selected ({n})" if n else "Reject Selected")
        self._accept_sel_price_btn.setEnabled(n > 0)
        self._reject_sel_price_btn.setEnabled(n > 0)

    # ------------------------------------------------------------------
    # Row-level actions
    # ------------------------------------------------------------------

    def _accept_xref(self, xid: int) -> None:
        accept_scraped_xref(xid)
        self._load_xrefs()

    def _reject_xref(self, xid: int) -> None:
        reject_scraped_xref(xid)
        self._load_xrefs()

    def _accept_price(self, pid: int) -> None:
        accept_competitor_price(pid)
        self._load_prices()

    def _reject_price(self, pid: int) -> None:
        reject_competitor_price(pid)
        self._load_prices()

    # ------------------------------------------------------------------
    # Batch actions
    # ------------------------------------------------------------------

    def _accept_selected_xrefs(self) -> None:
        for xid in self._checked_xref_ids():
            accept_scraped_xref(xid)
        self._load_xrefs()

    def _reject_selected_xrefs(self) -> None:
        for xid in self._checked_xref_ids():
            reject_scraped_xref(xid)
        self._load_xrefs()

    def _accept_all_xrefs(self) -> None:
        for row in range(self._xref_table.rowCount()):
            item = self._xref_table.item(row, 0)
            if item:
                accept_scraped_xref(item.data(Qt.ItemDataRole.UserRole))
        self._load_xrefs()

    def _reject_all_xrefs(self) -> None:
        for row in range(self._xref_table.rowCount()):
            item = self._xref_table.item(row, 0)
            if item:
                reject_scraped_xref(item.data(Qt.ItemDataRole.UserRole))
        self._load_xrefs()

    def _accept_selected_prices(self) -> None:
        for pid in self._checked_price_ids():
            accept_competitor_price(pid)
        self._load_prices()

    def _reject_selected_prices(self) -> None:
        for pid in self._checked_price_ids():
            reject_competitor_price(pid)
        self._load_prices()

    def _accept_all_prices(self) -> None:
        for row in range(self._price_table.rowCount()):
            item = self._price_table.item(row, 0)
            if item:
                accept_competitor_price(item.data(Qt.ItemDataRole.UserRole))
        self._load_prices()

    def _reject_all_prices(self) -> None:
        for row in range(self._price_table.rowCount()):
            item = self._price_table.item(row, 0)
            if item:
                reject_competitor_price(item.data(Qt.ItemDataRole.UserRole))
        self._load_prices()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _action_buttons(on_accept, on_reject) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(4)
        acc = QPushButton("Accept")
        acc.setStyleSheet(
            "QPushButton { background-color: #4a6741; color: white; font-size: 8pt; "
            "padding: 2px 8px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #5a7a51; }"
        )
        acc.setToolTip("Accept this cross-reference")
        acc.setFixedHeight(22)
        acc.clicked.connect(on_accept)
        rej = QPushButton("Reject")
        rej.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; font-size: 8pt; "
            "padding: 2px 8px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #d32f2f; }"
        )
        rej.setToolTip("Reject this cross-reference")
        rej.setFixedHeight(22)
        rej.clicked.connect(on_reject)
        lay.addWidget(acc)
        lay.addWidget(rej)
        return w


def _ro_item(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it
