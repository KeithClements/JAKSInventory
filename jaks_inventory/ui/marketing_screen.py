"""Marketing screen â€“ SMS campaigns with advanced filter builder,
template library, saved audiences, scheduling, and campaign history."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QFormLayout,
    QMessageBox,
    QProgressBar,
    QCheckBox,
    QLineEdit,
    QTabWidget,
    QSplitter,
    QDialog,
    QDialogButtonBox,
    QScrollArea,
    QFrame,
    QDateTimeEdit,
)
from PySide6.QtGui import QFont

from db import (
    get_all_customers,
    get_sms_templates,
    get_sms_template,
    create_sms_template,
    update_sms_template,
    delete_sms_template,
    get_saved_audiences,
    get_saved_audience,
    create_saved_audience,
    delete_saved_audience,
    create_campaign,
    get_campaigns,
    update_campaign,
    add_campaign_recipient,
    update_campaign_recipient,
    filter_customers_by_criteria,
    get_conversation_stats,
)
from ..utils.notification_service import (
    _send_sms,
    _send_email,
    email_configured,
)
from ..utils.sms_service import is_configured as sms_configured

log = logging.getLogger(__name__)

# â”€â”€ Filter definitions â”€â”€
_FILTER_TYPES = [
    ("-- Select Filter --", ""),
    ("Bought in last X days", "last_order_within"),
    ("No order in X days", "no_order_since"),
    ("Bought specific part", "bought_part"),
    ("Bought category", "bought_category"),
    ("Top spenders (last X days)", "top_spenders"),
    ("Engine type (C15, ISXâ€¦)", "engine_type"),
    ("Open balance", "open_balance"),
    ("Over credit limit", "over_credit"),
    ("Invoices > X days unpaid", "aging_days"),
    ("Payment terms", "payment_terms"),
    ("Open cores", "open_cores"),
    ("Overdue cores (X days)", "overdue_cores"),
    ("Core credits available", "core_credits"),
    ("Customer type", "customer_type"),
    ("Has phone", "has_phone"),
    ("Has email", "has_email"),
    ("Open quotes", "open_quotes"),
]

_TEMPLATE_CATEGORIES = [
    "All", "sales", "urgency", "follow_up", "core",
    "payment", "reactivation", "upsell", "general",
]

_CUSTOMER_TYPES = ["Fleet", "Shop", "Dealer", "Mobile Mechanic", "Wholesale"]

_CARD_SS = (
    "QFrame { background: #2b2b2b; border: 1px solid #3a3a3a; "
    "border-radius: 6px; padding: 8px; }"
)
_KPI_NUM = "font-size: 20pt; font-weight: bold; color: #6B8E23;"
_KPI_LBL = "font-size: 8pt; color: #999;"


class MarketingScreen(QWidget):
    """Marketing campaign management with advanced segmentation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filters: list[dict] = []
        self._matched_customers: list[dict] = []
        self._build_ui()
        self._refresh_campaign_history()

    # ==================================================================
    #  UI
    # ==================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        hdr = QHBoxLayout()
        title = QLabel("ðŸ“£ Marketing & Campaigns")
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        title.setFont(f)
        hdr.addWidget(title)
        hdr.addStretch()

        self._kpi_conversations = QLabel("0")
        self._kpi_unread = QLabel("0")
        self._kpi_campaigns = QLabel("0")
        for lbl, caption in [
            (self._kpi_conversations, "Conversations"),
            (self._kpi_unread, "Unread"),
            (self._kpi_campaigns, "Campaigns"),
        ]:
            card = QFrame()
            card.setStyleSheet(_CARD_SS)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 4, 12, 4)
            lbl.setStyleSheet(_KPI_NUM)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(lbl)
            cap = QLabel(caption)
            cap.setStyleSheet(_KPI_LBL)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(cap)
            hdr.addWidget(card)

        root.addLayout(hdr)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_campaign_tab(), "ðŸŽ¯ Campaign Builder")
        self._tabs.addTab(self._build_templates_tab(), "ðŸ“ Templates")
        self._tabs.addTab(self._build_history_tab(), "ðŸ“Š Campaign History")
        root.addWidget(self._tabs)

        self._refresh_kpis()

    # ------------------------------------------------------------------
    #  Tab 1: Campaign Builder
    # ------------------------------------------------------------------
    def _build_campaign_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # â”€â”€ Left: filter builder + results â”€â”€
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)

        filter_group = QGroupBox("ðŸ” Audience Filters (AND logic)")
        fg_lay = QVBoxLayout()

        self._filter_scroll = QScrollArea()
        self._filter_scroll.setWidgetResizable(True)
        self._filter_scroll.setMaximumHeight(200)
        self._filter_container = QWidget()
        self._filter_layout = QVBoxLayout(self._filter_container)
        self._filter_layout.setContentsMargins(0, 0, 0, 0)
        self._filter_layout.addStretch()
        self._filter_scroll.setWidget(self._filter_container)
        fg_lay.addWidget(self._filter_scroll)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("➕ Add Filter")
        add_btn.clicked.connect(self._add_filter_row)
        btn_row.addWidget(add_btn)
        clear_btn = QPushButton("ðŸ—‘ï¸ Clear All")
        clear_btn.clicked.connect(self._clear_filters)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()

        self._audience_combo = QComboBox()
        self._audience_combo.addItem("-- Saved Audiences --")
        self._audience_combo.currentIndexChanged.connect(self._on_load_audience)
        btn_row.addWidget(self._audience_combo)

        save_aud_btn = QPushButton("ðŸ’¾ Save Audience")
        save_aud_btn.clicked.connect(self._on_save_audience)
        btn_row.addWidget(save_aud_btn)
        fg_lay.addLayout(btn_row)

        apply_btn = QPushButton("ðŸ”Ž Find Matching Customers")
        apply_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; font-weight: bold; "
            "padding: 6px 16px; font-size: 10pt; }"
        )
        apply_btn.clicked.connect(self._apply_filters)
        fg_lay.addWidget(apply_btn)
        filter_group.setLayout(fg_lay)
        ll.addWidget(filter_group)

        result_group = QGroupBox("ðŸ‘¥ Matched Customers")
        rg_lay = QVBoxLayout()
        self._result_count = QLabel("0 customers matched")
        self._result_count.setStyleSheet("font-weight: bold; font-size: 10pt;")
        rg_lay.addWidget(self._result_count)

        self._cust_table = QTableWidget(0, 6)
        self._cust_table.setHorizontalHeaderLabels(
            ["", "Name", "Company", "Phone", "Email", "Type"]
        )
        self._cust_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self._cust_table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._cust_table.setColumnWidth(0, 40)
        rg_lay.addWidget(self._cust_table)

        sel_row = QHBoxLayout()
        sa = QPushButton("Select All")
        sa.clicked.connect(lambda: self._toggle_all(True))
        sel_row.addWidget(sa)
        sn = QPushButton("Select None")
        sn.clicked.connect(lambda: self._toggle_all(False))
        sel_row.addWidget(sn)
        sel_row.addStretch()
        self._selected_label = QLabel("0 selected")
        sel_row.addWidget(self._selected_label)
        rg_lay.addLayout(sel_row)
        result_group.setLayout(rg_lay)
        ll.addWidget(result_group)
        splitter.addWidget(left)

        # â”€â”€ Right: message composer â”€â”€
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        nr = QHBoxLayout()
        nr.addWidget(QLabel("Campaign Name:"))
        self._campaign_name = QLineEdit()
        self._campaign_name.setPlaceholderText("e.g. C15 Head Restock Alert")
        nr.addWidget(self._campaign_name)
        rl.addLayout(nr)

        cr = QHBoxLayout()
        cr.addWidget(QLabel("Channel:"))
        self._channel_combo = QComboBox()
        self._channel_combo.addItems(["ðŸ“± SMS", "ðŸ“§ Email"])
        cr.addWidget(self._channel_combo)
        cr.addWidget(QLabel("Subject:"))
        self._subject_edit = QLineEdit()
        self._subject_edit.setPlaceholderText("Email subject (optional for SMS)")
        cr.addWidget(self._subject_edit)
        rl.addLayout(cr)

        tr = QHBoxLayout()
        tr.addWidget(QLabel("Template:"))
        self._tpl_combo = QComboBox()
        self._tpl_combo.addItem("-- Select Template --", 0)
        self._tpl_combo.currentIndexChanged.connect(self._on_insert_template)
        tr.addWidget(self._tpl_combo, 1)
        rl.addLayout(tr)

        self._msg_edit = QTextEdit()
        self._msg_edit.setPlaceholderText(
            "Type your message here...\n\n"
            "Variables: {name}, {company}, {part}\n"
            "Example: Hey {name}, just got C15 heads back in stock!"
        )
        self._msg_edit.setMaximumHeight(180)
        rl.addWidget(self._msg_edit)

        sr = QHBoxLayout()
        self._schedule_check = QCheckBox("Schedule for later")
        sr.addWidget(self._schedule_check)
        self._schedule_dt = QDateTimeEdit()
        self._schedule_dt.setCalendarPopup(True)
        self._schedule_dt.setDateTime(datetime.now())
        self._schedule_dt.setEnabled(False)
        self._schedule_check.toggled.connect(self._schedule_dt.setEnabled)
        sr.addWidget(self._schedule_dt)
        sr.addStretch()
        rl.addLayout(sr)

        ar = QHBoxLayout()
        ar.addStretch()
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setMinimumWidth(200)
        ar.addWidget(self._progress)
        self._send_btn = QPushButton("ðŸš€ Send Campaign")
        self._send_btn.setStyleSheet(
            "QPushButton { background: #6B8E23; color: white; font-weight: bold; "
            "padding: 8px 24px; font-size: 11pt; }"
        )
        self._send_btn.clicked.connect(self._on_send_campaign)
        ar.addWidget(self._send_btn)
        rl.addLayout(ar)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        self._refresh_audiences()
        self._refresh_template_combo()
        return w

    # ------------------------------------------------------------------
    #  Tab 2: Template Library
    # ------------------------------------------------------------------
    def _build_templates_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        top = QHBoxLayout()
        top.addWidget(QLabel("Category:"))
        self._tpl_cat_filter = QComboBox()
        self._tpl_cat_filter.addItems(_TEMPLATE_CATEGORIES)
        self._tpl_cat_filter.currentTextChanged.connect(self._load_templates)
        top.addWidget(self._tpl_cat_filter)
        top.addStretch()
        add_btn = QPushButton("➕ New Template")
        add_btn.setStyleSheet(
            "QPushButton { background: #6B8E23; color: white; font-weight: bold; padding: 4px 12px; }"
        )
        add_btn.clicked.connect(self._on_new_template)
        top.addWidget(add_btn)
        layout.addLayout(top)

        self._tpl_table = QTableWidget(0, 4)
        self._tpl_table.setHorizontalHeaderLabels(["Name", "Category", "Message", "Actions"])
        self._tpl_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self._tpl_table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._tpl_table.setColumnWidth(0, 180)
        self._tpl_table.setColumnWidth(1, 100)
        self._tpl_table.setColumnWidth(3, 160)
        layout.addWidget(self._tpl_table)

        self._load_templates()
        return w

    # ------------------------------------------------------------------
    #  Tab 3: Campaign History
    # ------------------------------------------------------------------
    def _build_history_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        top = QHBoxLayout()
        top.addWidget(QLabel("Filter:"))
        self._hist_filter = QComboBox()
        self._hist_filter.addItems(["All", "sent", "draft", "scheduled"])
        self._hist_filter.currentTextChanged.connect(self._refresh_campaign_history)
        top.addWidget(self._hist_filter)
        top.addStretch()
        ref_btn = QPushButton("ðŸ”„ Refresh")
        ref_btn.clicked.connect(self._refresh_campaign_history)
        top.addWidget(ref_btn)
        layout.addLayout(top)

        self._hist_table = QTableWidget(0, 7)
        self._hist_table.setHorizontalHeaderLabels(
            ["ID", "Name", "Channel", "Status", "Recipients", "Sent/Failed", "Date"]
        )
        self._hist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self._hist_table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._hist_table.setColumnWidth(0, 50)
        layout.addWidget(self._hist_table)
        return w

    # ==================================================================
    #  Filter Builder
    # ==================================================================
    def _add_filter_row(self) -> None:
        row_w = QWidget()
        rl = QHBoxLayout(row_w)
        rl.setContentsMargins(0, 2, 0, 2)

        type_combo = QComboBox()
        for label, ftype in _FILTER_TYPES:
            type_combo.addItem(label, ftype)
        rl.addWidget(type_combo, 2)

        value_edit = QLineEdit()
        value_edit.setPlaceholderText("Value (days, keyword, typeâ€¦)")
        rl.addWidget(value_edit, 2)

        type_helper = QComboBox()
        type_helper.addItems([""] + _CUSTOMER_TYPES + ["COD"])
        type_helper.setVisible(False)
        type_helper.currentTextChanged.connect(value_edit.setText)
        rl.addWidget(type_helper)

        def _on_type(idx):
            ft = type_combo.currentData()
            needs = ft not in ("", "open_balance", "over_credit", "open_cores",
                               "core_credits", "has_phone", "has_email", "open_quotes")
            value_edit.setVisible(needs)
            type_helper.setVisible(ft == "customer_type")
            if ft == "payment_terms":
                value_edit.setPlaceholderText("e.g. COD, Net 30")
            elif ft in ("last_order_within", "no_order_since", "top_spenders",
                        "aging_days", "overdue_cores"):
                value_edit.setPlaceholderText("Number of days")
            elif ft in ("bought_part", "engine_type"):
                value_edit.setPlaceholderText("Keyword (e.g. C15, injector)")
            elif ft == "bought_category":
                value_edit.setPlaceholderText("Category name")

        type_combo.currentIndexChanged.connect(_on_type)

        rm_btn = QPushButton("âœ•")
        rm_btn.setFixedWidth(30)
        rm_btn.setStyleSheet("color: red; font-weight: bold;")
        rm_btn.clicked.connect(lambda: self._remove_filter_row(row_w))
        rl.addWidget(rm_btn)

        row_w._type_combo = type_combo
        row_w._value_edit = value_edit
        self._filter_layout.insertWidget(self._filter_layout.count() - 1, row_w)

    def _remove_filter_row(self, w: QWidget) -> None:
        self._filter_layout.removeWidget(w)
        w.deleteLater()

    def _clear_filters(self) -> None:
        while self._filter_layout.count() > 1:
            child = self._filter_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _collect_filters(self) -> list[dict]:
        filters = []
        for i in range(self._filter_layout.count()):
            item = self._filter_layout.itemAt(i)
            if not item or not item.widget():
                continue
            w = item.widget()
            if not hasattr(w, "_type_combo"):
                continue
            ftype = w._type_combo.currentData()
            if not ftype:
                continue
            filters.append({"type": ftype, "value": w._value_edit.text().strip()})
        return filters

    def _apply_filters(self) -> None:
        filters = self._collect_filters()
        if not filters:
            customers = [c for c in (get_all_customers() or []) if c.get("is_active", True)]
        else:
            customers = filter_customers_by_criteria(filters)
        self._matched_customers = customers
        self._populate_customer_table(customers)

    def _populate_customer_table(self, customers: list[dict]) -> None:
        self._cust_table.setRowCount(len(customers))
        for row, c in enumerate(customers):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked)
            self._cust_table.setItem(row, 0, chk)

            def _it(text):
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._cust_table.setItem(row, 1, _it(c.get("name", "")))
            self._cust_table.setItem(row, 2, _it(c.get("company", "")))
            self._cust_table.setItem(row, 3, _it(c.get("phone", "")))
            self._cust_table.setItem(row, 4, _it(c.get("email", "")))
            self._cust_table.setItem(row, 5, _it(c.get("customer_type", "")))

        self._result_count.setText(f"{len(customers)} customers matched")
        self._update_selected_count()

    def _toggle_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self._cust_table.rowCount()):
            item = self._cust_table.item(row, 0)
            if item:
                item.setCheckState(state)
        self._update_selected_count()

    def _update_selected_count(self) -> None:
        count = sum(
            1 for row in range(self._cust_table.rowCount())
            if self._cust_table.item(row, 0)
            and self._cust_table.item(row, 0).checkState() == Qt.CheckState.Checked
        )
        self._selected_label.setText(f"{count} selected")

    def _get_checked_customers(self) -> list[dict]:
        result = []
        for row in range(self._cust_table.rowCount()):
            chk = self._cust_table.item(row, 0)
            if chk and chk.checkState() == Qt.CheckState.Checked:
                if row < len(self._matched_customers):
                    result.append(self._matched_customers[row])
        return result

    # ==================================================================
    #  Saved Audiences
    # ==================================================================
    def _refresh_audiences(self) -> None:
        self._audience_combo.blockSignals(True)
        self._audience_combo.clear()
        self._audience_combo.addItem("-- Saved Audiences --", 0)
        for a in get_saved_audiences():
            self._audience_combo.addItem(
                f"{a['name']} ({a.get('customer_count', 0)})", a["id"]
            )
        self._audience_combo.blockSignals(False)

    def _on_load_audience(self, idx: int) -> None:
        aid = self._audience_combo.currentData()
        if not aid:
            return
        audience = get_saved_audience(aid)
        if not audience:
            return
        self._clear_filters()
        try:
            filters = json.loads(audience.get("filters_json", "[]"))
        except Exception:
            filters = []
        for f in filters:
            self._add_filter_row()
            row_w = self._filter_layout.itemAt(self._filter_layout.count() - 2).widget()
            ftype = f.get("type", "")
            for i in range(row_w._type_combo.count()):
                if row_w._type_combo.itemData(i) == ftype:
                    row_w._type_combo.setCurrentIndex(i)
                    break
            row_w._value_edit.setText(f.get("value", ""))
        self._apply_filters()

    def _on_save_audience(self) -> None:
        filters = self._collect_filters()
        if not filters:
            QMessageBox.warning(self, "No Filters", "Add at least one filter before saving.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Save Audience")
        dlg.setMinimumWidth(350)
        fl = QFormLayout(dlg)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("e.g. Fleet customers with open cores")
        fl.addRow("Name:", name_edit)
        desc_edit = QLineEdit()
        desc_edit.setPlaceholderText("Optional description")
        fl.addRow("Description:", desc_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        fl.addRow(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        if not name:
            return
        count = len(self._matched_customers)
        create_saved_audience(name, desc_edit.text().strip(), json.dumps(filters), count)
        self._refresh_audiences()
        QMessageBox.information(self, "Saved", f"Audience '{name}' saved with {count} customers.")

    # ==================================================================
    #  Templates
    # ==================================================================
    def _refresh_template_combo(self) -> None:
        self._tpl_combo.blockSignals(True)
        self._tpl_combo.clear()
        self._tpl_combo.addItem("-- Select Template --", 0)
        for t in get_sms_templates():
            self._tpl_combo.addItem(f"[{t['category']}] {t['name']}", t["id"])
        self._tpl_combo.blockSignals(False)

    def _on_insert_template(self, idx: int) -> None:
        tid = self._tpl_combo.currentData()
        if not tid:
            return
        tpl = get_sms_template(tid)
        if tpl:
            self._msg_edit.setPlainText(tpl["body"])

    def _load_templates(self) -> None:
        cat = self._tpl_cat_filter.currentText() if hasattr(self, "_tpl_cat_filter") else "All"
        templates = get_sms_templates(None if cat == "All" else cat)
        self._tpl_table.setRowCount(len(templates))
        for row, t in enumerate(templates):
            def _it(text):
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._tpl_table.setItem(row, 0, _it(t["name"]))
            self._tpl_table.setItem(row, 1, _it(t["category"]))
            self._tpl_table.setItem(row, 2, _it(t["body"]))

            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(2, 2, 2, 2)
            tid = t["id"]

            edit_btn = QPushButton("âœï¸")
            edit_btn.setToolTip("Edit")
            edit_btn.setFixedWidth(30)
            edit_btn.clicked.connect(lambda _, tid=tid: self._template_dialog(tid))
            al.addWidget(edit_btn)

            del_btn = QPushButton("ðŸ—‘ï¸")
            del_btn.setToolTip("Delete")
            del_btn.setFixedWidth(30)
            del_btn.clicked.connect(lambda _, tid=tid: self._on_delete_template(tid))
            al.addWidget(del_btn)

            use_btn = QPushButton("ðŸ“‹")
            use_btn.setToolTip("Use in campaign")
            use_btn.setFixedWidth(30)
            use_btn.clicked.connect(lambda _, b=t["body"]: self._msg_edit.setPlainText(b))
            al.addWidget(use_btn)

            self._tpl_table.setCellWidget(row, 3, actions)

    def _on_new_template(self) -> None:
        self._template_dialog(None)

    def _template_dialog(self, tid: int | None) -> None:
        tpl = get_sms_template(tid) if tid else None
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Template" if tpl else "New Template")
        dlg.setMinimumWidth(450)
        fl = QFormLayout(dlg)
        name_edit = QLineEdit(tpl["name"] if tpl else "")
        fl.addRow("Name:", name_edit)
        cat_combo = QComboBox()
        cat_combo.addItems([c for c in _TEMPLATE_CATEGORIES if c != "All"])
        if tpl:
            idx = cat_combo.findText(tpl["category"])
            if idx >= 0:
                cat_combo.setCurrentIndex(idx)
        fl.addRow("Category:", cat_combo)
        body_edit = QTextEdit(tpl["body"] if tpl else "")
        body_edit.setPlaceholderText("Hey {name}, ...")
        body_edit.setMaximumHeight(120)
        fl.addRow("Message:", body_edit)
        var_lbl = QLabel("Variables: {name}, {company}, {part}")
        var_lbl.setStyleSheet("color: #999; font-size: 8pt;")
        fl.addRow("", var_lbl)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        fl.addRow(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        body = body_edit.toPlainText().strip()
        cat = cat_combo.currentText()
        if not name or not body:
            return
        if tpl:
            update_sms_template(tid, name=name, category=cat, body=body)
        else:
            create_sms_template(name, cat, body)
        self._load_templates()
        self._refresh_template_combo()

    def _on_delete_template(self, tid: int) -> None:
        if QMessageBox.question(
            self, "Delete", "Delete this template?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            delete_sms_template(tid)
            self._load_templates()
            self._refresh_template_combo()

    # ==================================================================
    #  Campaign History
    # ==================================================================
    def _refresh_campaign_history(self) -> None:
        sf = None
        if hasattr(self, "_hist_filter"):
            v = self._hist_filter.currentText()
            if v != "All":
                sf = v
        campaigns = get_campaigns(sf)
        self._hist_table.setRowCount(len(campaigns))
        for row, c in enumerate(campaigns):
            def _it(text):
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._hist_table.setItem(row, 0, _it(c.get("id", "")))
            self._hist_table.setItem(row, 1, _it(c.get("name", "")))
            self._hist_table.setItem(row, 2, _it(c.get("channel", "")))
            si = _it((c.get("status") or "").upper())
            status = c.get("status", "")
            if status == "sent":
                si.setForeground(Qt.GlobalColor.green)
            elif status == "draft":
                si.setForeground(Qt.GlobalColor.yellow)
            elif status == "scheduled":
                si.setForeground(Qt.GlobalColor.cyan)
            self._hist_table.setItem(row, 3, si)
            self._hist_table.setItem(row, 4, _it(c.get("total_recipients", 0)))
            s = c.get("total_sent", 0)
            fa = c.get("total_failed", 0)
            self._hist_table.setItem(row, 5, _it(f"âœ… {s} / âŒ {fa}"))
            self._hist_table.setItem(row, 6, _it(
                c.get("sent_at") or c.get("scheduled_at") or c.get("created_at", "")
            ))
        if hasattr(self, "_kpi_campaigns"):
            self._kpi_campaigns.setText(str(len(get_campaigns())))

    # ==================================================================
    #  Send Campaign
    # ==================================================================
    def _on_send_campaign(self) -> None:
        msg_tpl = self._msg_edit.toPlainText().strip()
        if not msg_tpl:
            QMessageBox.warning(self, "No Message", "Please write a message first.")
            return
        targets = self._get_checked_customers()
        if not targets:
            QMessageBox.warning(self, "No Recipients", "Select at least one customer.")
            return
        channel = self._channel_combo.currentText()
        is_sms = "SMS" in channel
        cname = self._campaign_name.text().strip() or f"Campaign {datetime.now():%Y-%m-%d %H:%M}"

        if is_sms and not sms_configured():
            QMessageBox.warning(self, "SMS Not Configured",
                                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and "
                                "TWILIO_PHONE_NUMBER in your .env file.")
            return
        if not is_sms and not email_configured():
            QMessageBox.warning(self, "Email Not Configured",
                                "Configure SMTP settings in Settings â†’ Email/SMTP.")
            return
        subject = self._subject_edit.text().strip()
        if not is_sms and not subject:
            QMessageBox.warning(self, "No Subject", "Enter an email subject line.")
            return

        if self._schedule_check.isChecked():
            sdt = self._schedule_dt.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            camp_id = create_campaign(cname, "sms" if is_sms else "email",
                                      msg_tpl, subject, scheduled_at=sdt)
            for c in targets:
                add_campaign_recipient(camp_id, c["id"], c.get("phone", ""), c.get("email", ""))
            update_campaign(camp_id, total_recipients=len(targets), status="scheduled")
            self._refresh_campaign_history()
            QMessageBox.information(
                self, "Scheduled",
                f"Campaign '{cname}' scheduled for {sdt} with {len(targets)} recipients."
            )
            return

        # Confirm immediate send
        if QMessageBox.question(
            self, "Send Now?",
            f"Send {channel} to {len(targets)} customers?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        camp_id = create_campaign(cname, "sms" if is_sms else "email", msg_tpl, subject)
        for c in targets:
            add_campaign_recipient(camp_id, c["id"], c.get("phone", ""), c.get("email", ""))
        update_campaign(camp_id, total_recipients=len(targets), status="sending")

        self._progress.setVisible(True)
        self._progress.setMaximum(len(targets))
        self._progress.setValue(0)
        self._send_btn.setEnabled(False)

        sent, failed = 0, 0
        for i, c in enumerate(targets):
            name = c.get("name", "Customer")
            company = c.get("company", "")
            msg = msg_tpl.replace("{name}", name).replace("{company}", company)
            recip_id = None
            try:
                recs = get_campaign_recipients(camp_id)
                for r in recs:
                    if r["customer_id"] == c["id"]:
                        recip_id = r["id"]
                        break
            except Exception:
                pass

            ok = False
            error = ""
            try:
                if is_sms:
                    phone = c.get("phone", "")
                    if phone:
                        _send_sms(phone, msg)
                        ok = True
                    else:
                        error = "No phone number"
                else:
                    email = c.get("email", "")
                    if email:
                        _send_email(email, subject, msg)
                        ok = True
                    else:
                        error = "No email address"
            except Exception as e:
                error = str(e)[:200]
                log.warning("Campaign send error for %s: %s", name, e)

            if ok:
                sent += 1
                if recip_id:
                    update_campaign_recipient(recip_id, status="sent")
            else:
                failed += 1
                if recip_id:
                    update_campaign_recipient(recip_id, status="failed", error=error)

            self._progress.setValue(i + 1)

        update_campaign(camp_id, total_sent=sent, total_failed=failed, status="sent")
        self._progress.setVisible(False)
        self._send_btn.setEnabled(True)
        self._refresh_campaign_history()
        QMessageBox.information(
            self, "Campaign Complete",
            f"Sent: {sent}  |  Failed: {failed}"
        )

    # ==================================================================
    #  KPIs
    # ==================================================================
    def _refresh_kpis(self) -> None:
        try:
            stats = get_conversation_stats()
            self._kpi_conversations.setText(str(stats.get("total", 0)))
            self._kpi_unread.setText(str(stats.get("unread", 0)))
        except Exception:
            pass
        try:
            self._kpi_campaigns.setText(str(len(get_campaigns())))
        except Exception:
            pass

    def refresh(self) -> None:
        self._refresh_kpis()
        self._refresh_campaign_history()
        self._refresh_audiences()
        self._refresh_template_combo()
        if hasattr(self, "_tpl_cat_filter"):
            self._load_templates()

            QMessageBox.information(self, "Scheduled",
                                    f"'{cname}' scheduled for {sdt} with {len(targets)} recipients.")
            return

        if QMessageBox.question(
            self, "Confirm Send",
            f"Send {channel} to {len(targets)} customer(s)?\n\n"
            f"Preview:\n{msg_tpl[:200]}{'...' if len(msg_tpl) > 200 else ''}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        camp_id = create_campaign(cname, "sms" if is_sms else "email", msg_tpl, subject)
        self._progress.setVisible(True)
        self._progress.setRange(0, len(targets))
        self._send_btn.setEnabled(False)

        sent = failed = 0
        errors = []
        for i, cust in enumerate(targets):
            name = cust.get("name", "Customer")
            company = cust.get("company", "")
            body = msg_tpl.replace("{name}", name).replace("{company}", company)
            rid = add_campaign_recipient(camp_id, cust["id"],
                                         cust.get("phone", ""), cust.get("email", ""))
            if is_sms:
                phone = cust.get("phone", "")
                if not phone:
                    failed += 1
                    update_campaign_recipient(rid, status="failed", error="no phone")
                    errors.append(f"{name}: no phone")
                    self._progress.setValue(i + 1)
                    continue
                result = _send_sms(phone, body)
            else:
                email = cust.get("email", "")
                if not email:
                    failed += 1
                    update_campaign_recipient(rid, status="failed", error="no email")
                    errors.append(f"{name}: no email")
                    self._progress.setValue(i + 1)
                    continue
                html = f"<div style='font-family:Arial,sans-serif;'><p>{body.replace(chr(10), '<br>')}</p></div>"
                result = _send_email(email, subject, html)

            if result.get("success"):
                sent += 1
                update_campaign_recipient(rid, status="sent", sent_at=datetime.now().isoformat())
            else:
                failed += 1
                update_campaign_recipient(rid, status="failed",
                                          error=result.get("error", "unknown"))
                errors.append(f"{name}: {result.get('error', 'unknown')}")
            self._progress.setValue(i + 1)

        update_campaign(camp_id, status="sent", sent_at=datetime.now().isoformat(),
                        total_recipients=len(targets), total_sent=sent, total_failed=failed)
        self._send_btn.setEnabled(True)
        self._progress.setVisible(False)

        summary = f"Campaign complete!\n\nâœ… Sent: {sent}\nâŒ Failed: {failed}"
        if errors:
            summary += "\n\nErrors:\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                summary += f"\n... +{len(errors) - 10} more"
        QMessageBox.information(self, "Campaign Results", summary)
        self._refresh_campaign_history()
        self._refresh_kpis()

    # ==================================================================
    #  KPIs
    # ==================================================================
    def _refresh_kpis(self) -> None:
        try:
            stats = get_conversation_stats()
            self._kpi_conversations.setText(str(stats.get("total_conversations", 0)))
            self._kpi_unread.setText(str(stats.get("unread", 0)))
        except Exception:
            pass
        try:
            self._kpi_campaigns.setText(str(len(get_campaigns())))
        except Exception:
            pass

    def refresh(self) -> None:
        self._refresh_kpis()
        self._refresh_campaign_history()
        self._refresh_audiences()
        self._refresh_template_combo()
        if hasattr(self, "_tpl_cat_filter"):
            self._load_templates()

