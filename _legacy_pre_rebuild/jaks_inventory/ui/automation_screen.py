"""Automation Engine screen — rule management, suggestion log, and one-click actions."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QFormLayout,
    QMessageBox,
    QCheckBox,
    QLineEdit,
    QSplitter,
    QFrame,
    QTextEdit,
    QDialog,
    QDialogButtonBox,
    QSpinBox,
)
from PySide6.QtGui import QFont, QColor

from db import (
    get_automation_rules,
    get_automation_rule,
    update_automation_rule,
    add_automation_log,
    get_automation_log,
    update_automation_log_status,
    get_stale_quotes,
    get_inactive_customers,
    get_customers_near_credit_limit,
    get_sms_templates,
    get_sms_template,
)
from ..utils.sms_service import is_configured as sms_configured

try:
    from ..utils.notification_service import _send_sms
except ImportError:
    _send_sms = None

log = logging.getLogger(__name__)

_CARD_SS = (
    "QFrame { background: #2b2b2b; border: 1px solid #3a3a3a; "
    "border-radius: 6px; padding: 8px; }"
)
_KPI_NUM = "font-size: 20pt; font-weight: bold; color: #6B8E23;"
_KPI_LBL = "font-size: 8pt; color: #999;"

_RULE_LABELS = {
    "quote_followup": "📋 Quote Follow-Up",
    "core_overdue": "🔧 Core Overdue",
    "inactive_customer": "😴 Inactive Customer",
    "restock_notification": "📦 Restock Notification",
    "credit_warning": "💳 Credit Warning",
    "open_core_warning": "⚠️ Open Core Warning",
}

_STATUS_COLORS = {
    "suggested": QColor("#FFA726"),
    "sent": QColor("#66BB6A"),
    "dismissed": QColor("#999999"),
    "failed": QColor("#EF5350"),
}


class AutomationScreen(QWidget):
    """Automation engine — rules, suggestions, and one-click SMS actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_rules()
        self._load_suggestions()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("🤖 Automation Engine")
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        title.setFont(f)
        hdr.addWidget(title)
        hdr.addStretch()

        # KPI cards
        self._kpi_active = QLabel("0")
        self._kpi_pending = QLabel("0")
        self._kpi_sent = QLabel("0")
        for lbl, caption in [
            (self._kpi_active, "Active Rules"),
            (self._kpi_pending, "Pending"),
            (self._kpi_sent, "Sent Today"),
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

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top: Rules table
        rules_w = QWidget()
        rules_lay = QVBoxLayout(rules_w)
        rules_lay.setContentsMargins(0, 0, 0, 0)

        rules_hdr = QHBoxLayout()
        rules_hdr.addWidget(QLabel("⚙️ Automation Rules"))
        rules_hdr.addStretch()
        run_btn = QPushButton("▶️ Run All Rules Now")
        run_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; font-weight: bold; "
            "padding: 6px 16px; }"
        )
        run_btn.clicked.connect(self._run_all_rules)
        rules_hdr.addWidget(run_btn)
        rules_lay.addLayout(rules_hdr)

        self._rules_table = QTableWidget(0, 6)
        self._rules_table.setHorizontalHeaderLabels(
            ["Active", "Rule", "Type", "Config", "Template", "Last Run"]
        )
        self._rules_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        hh = self._rules_table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._rules_table.setColumnWidth(0, 60)
        self._rules_table.setColumnWidth(2, 140)
        self._rules_table.setColumnWidth(4, 180)
        self._rules_table.setColumnWidth(5, 140)
        rules_lay.addWidget(self._rules_table)
        splitter.addWidget(rules_w)

        # Bottom: Suggestions log
        sugg_w = QWidget()
        sugg_lay = QVBoxLayout(sugg_w)
        sugg_lay.setContentsMargins(0, 0, 0, 0)

        sugg_hdr = QHBoxLayout()
        sugg_hdr.addWidget(QLabel("📬 Suggestions & Actions"))
        sugg_hdr.addStretch()
        sugg_hdr.addWidget(QLabel("Filter:"))
        self._log_filter = QComboBox()
        self._log_filter.addItems(["All", "suggested", "sent", "dismissed", "failed"])
        self._log_filter.currentTextChanged.connect(self._load_suggestions)
        sugg_hdr.addWidget(self._log_filter)
        ref_btn = QPushButton("🔄 Refresh")
        ref_btn.clicked.connect(self._load_suggestions)
        sugg_hdr.addWidget(ref_btn)
        sugg_lay.addLayout(sugg_hdr)

        self._log_table = QTableWidget(0, 8)
        self._log_table.setHorizontalHeaderLabels(
            ["ID", "Rule", "Customer", "Phone", "Action", "Message", "Status", "Actions"]
        )
        self._log_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        hh2 = self._log_table.horizontalHeader()
        hh2.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._log_table.setColumnWidth(0, 50)
        self._log_table.setColumnWidth(1, 140)
        self._log_table.setColumnWidth(2, 140)
        self._log_table.setColumnWidth(3, 120)
        self._log_table.setColumnWidth(4, 100)
        self._log_table.setColumnWidth(6, 90)
        self._log_table.setColumnWidth(7, 200)
        sugg_lay.addWidget(self._log_table)
        splitter.addWidget(sugg_w)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

    # ==================================================================
    #  Rules
    # ==================================================================
    def _load_rules(self) -> None:
        rules = get_automation_rules()
        self._rules_table.setRowCount(len(rules))
        active_count = 0
        for row, r in enumerate(rules):
            # Active toggle
            chk = QCheckBox()
            chk.setChecked(bool(r.get("is_active")))
            rid = r["id"]
            chk.toggled.connect(lambda checked, rid=rid: self._toggle_rule(rid, checked))
            cw = QWidget()
            cl = QHBoxLayout(cw)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(chk)
            self._rules_table.setCellWidget(row, 0, cw)

            if r.get("is_active"):
                active_count += 1

            def _it(text):
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            rtype = r.get("rule_type", "")
            self._rules_table.setItem(row, 1, _it(r.get("name", "")))
            self._rules_table.setItem(row, 2, _it(_RULE_LABELS.get(rtype, rtype)))

            # Config summary
            try:
                cfg = json.loads(r.get("config_json", "{}"))
            except Exception:
                cfg = {}
            cfg_parts = []
            if "days_threshold" in cfg:
                cfg_parts.append(f"{cfg['days_threshold']} days")
            if "threshold_percent" in cfg:
                cfg_parts.append(f"{cfg['threshold_percent']}%")
            cfg_cell = _it(", ".join(cfg_parts) if cfg_parts else "—")
            self._rules_table.setItem(row, 3, cfg_cell)

            # Template
            tpl_name = "—"
            if r.get("template_id"):
                tpl = get_sms_template(r["template_id"])
                if tpl:
                    tpl_name = tpl["name"]
            self._rules_table.setItem(row, 4, _it(tpl_name))

            # Last run
            self._rules_table.setItem(row, 5, _it(r.get("last_run_at") or "Never"))

            # Config button
            cfg_btn = QPushButton("⚙️ Configure")
            cfg_btn.setFixedWidth(100)
            cfg_btn.clicked.connect(lambda _, rid=rid: self._configure_rule(rid))
            # We'll put configure in col 3 as widget if needed

        self._kpi_active.setText(str(active_count))

    def _toggle_rule(self, rule_id: int, active: bool) -> None:
        update_automation_rule(rule_id, is_active=1 if active else 0)
        self._refresh_kpis()

    def _configure_rule(self, rule_id: int) -> None:
        rule = get_automation_rule(rule_id)
        if not rule:
            return
        try:
            cfg = json.loads(rule.get("config_json", "{}"))
        except Exception:
            cfg = {}

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Configure: {rule['name']}")
        dlg.setMinimumWidth(400)
        fl = QFormLayout(dlg)

        # Days threshold
        days_spin = None
        if rule["rule_type"] in ("quote_followup", "core_overdue", "inactive_customer"):
            days_spin = QSpinBox()
            days_spin.setRange(1, 365)
            days_spin.setValue(cfg.get("days_threshold", 7))
            fl.addRow("Days threshold:", days_spin)

        # Threshold percent
        pct_spin = None
        if rule["rule_type"] == "credit_warning":
            pct_spin = QSpinBox()
            pct_spin.setRange(50, 100)
            pct_spin.setValue(cfg.get("threshold_percent", 90))
            pct_spin.setSuffix("%")
            fl.addRow("Credit limit threshold:", pct_spin)

        # Template
        tpl_combo = QComboBox()
        tpl_combo.addItem("-- No Template --", 0)
        for t in get_sms_templates():
            tpl_combo.addItem(f"[{t['category']}] {t['name']}", t["id"])
        if rule.get("template_id"):
            for i in range(tpl_combo.count()):
                if tpl_combo.itemData(i) == rule["template_id"]:
                    tpl_combo.setCurrentIndex(i)
                    break
        fl.addRow("SMS Template:", tpl_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        fl.addRow(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_cfg = dict(cfg)
        if days_spin is not None:
            new_cfg["days_threshold"] = days_spin.value()
        if pct_spin is not None:
            new_cfg["threshold_percent"] = pct_spin.value()

        tpl_id = tpl_combo.currentData() or None
        update_automation_rule(
            rule_id,
            config_json=json.dumps(new_cfg),
            template_id=tpl_id,
        )
        self._load_rules()
        QMessageBox.information(self, "Saved", f"Rule '{rule['name']}' updated.")

    # ==================================================================
    #  Run Rules
    # ==================================================================
    def _run_all_rules(self) -> None:
        rules = get_automation_rules()
        total_suggestions = 0

        for rule in rules:
            if not rule.get("is_active"):
                continue
            try:
                cfg = json.loads(rule.get("config_json", "{}"))
            except Exception:
                cfg = {}

            rtype = rule["rule_type"]
            customers = []
            action = ""

            if rtype == "quote_followup":
                days = cfg.get("days_threshold", 2)
                quotes = get_stale_quotes(days)
                for q in quotes:
                    customers.append({
                        "id": q["customer_id"],
                        "name": q.get("customer_name", ""),
                        "phone": q.get("phone", ""),
                    })
                action = "follow_up_quote"

            elif rtype == "inactive_customer":
                days = cfg.get("days_threshold", 60)
                customers = get_inactive_customers(days)
                action = "reactivate"

            elif rtype == "credit_warning":
                pct = cfg.get("threshold_percent", 90)
                customers = get_customers_near_credit_limit(pct)
                action = "credit_alert"

            elif rtype == "core_overdue":
                # Uses filter_customers_by_criteria for overdue cores
                try:
                    from db import filter_customers_by_criteria
                    days = cfg.get("days_threshold", 30)
                    customers = filter_customers_by_criteria(
                        [{"type": "overdue_cores", "value": str(days)}]
                    )
                    action = "core_reminder"
                except Exception:
                    pass

            elif rtype == "open_core_warning":
                try:
                    from db import filter_customers_by_criteria
                    customers = filter_customers_by_criteria(
                        [{"type": "open_cores"}]
                    )
                    action = "core_warning"
                except Exception:
                    pass

            # Build message from template if available
            tpl_body = ""
            if rule.get("template_id"):
                tpl = get_sms_template(rule["template_id"])
                if tpl:
                    tpl_body = tpl["body"]

            # Check existing pending suggestions to avoid duplicates
            existing = get_automation_log(rule_id=rule["id"], status="suggested")
            existing_cids = {e["customer_id"] for e in existing}

            for c in customers:
                cid = c.get("id") or c.get("customer_id")
                if not cid or cid in existing_cids:
                    continue
                name = c.get("name", c.get("customer_name", "Customer"))
                company = c.get("company", "")
                msg = tpl_body.replace("{name}", name).replace("{company}", company) if tpl_body else ""
                add_automation_log(rule["id"], cid, action, msg)
                total_suggestions += 1

            update_automation_rule(
                rule["id"],
                last_run_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

        self._load_rules()
        self._load_suggestions()
        QMessageBox.information(
            self, "Rules Executed",
            f"Scan complete — {total_suggestions} new suggestion(s) generated."
        )

    # ==================================================================
    #  Suggestions Log
    # ==================================================================
    def _load_suggestions(self) -> None:
        status_filter = None
        if hasattr(self, "_log_filter"):
            v = self._log_filter.currentText()
            if v != "All":
                status_filter = v

        entries = get_automation_log(status=status_filter, limit=200)
        self._log_table.setRowCount(len(entries))
        pending_count = 0
        sent_today = 0
        today = datetime.now().strftime("%Y-%m-%d")

        for row, e in enumerate(entries):
            def _it(text):
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._log_table.setItem(row, 0, _it(e.get("id", "")))
            self._log_table.setItem(row, 1, _it(
                _RULE_LABELS.get(e.get("rule_type", ""), e.get("rule_name", ""))
            ))
            self._log_table.setItem(row, 2, _it(e.get("customer_name", "")))
            self._log_table.setItem(row, 3, _it(e.get("phone", "")))
            self._log_table.setItem(row, 4, _it(e.get("action", "")))
            self._log_table.setItem(row, 5, _it(e.get("message", "")))

            status = e.get("status", "suggested")
            si = _it(status.upper())
            if status in _STATUS_COLORS:
                si.setForeground(_STATUS_COLORS[status])
            self._log_table.setItem(row, 6, si)

            if status == "suggested":
                pending_count += 1
            if status == "sent" and e.get("acted_at", "").startswith(today):
                sent_today += 1

            # Action buttons
            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(2, 2, 2, 2)
            eid = e["id"]

            if status == "suggested":
                send_btn = QPushButton("📱 Send")
                send_btn.setToolTip("Send SMS now")
                send_btn.setFixedWidth(70)
                send_btn.clicked.connect(
                    lambda _, eid=eid: self._on_send_suggestion(eid)
                )
                al.addWidget(send_btn)

                dismiss_btn = QPushButton("✕")
                dismiss_btn.setToolTip("Dismiss")
                dismiss_btn.setFixedWidth(30)
                dismiss_btn.setStyleSheet("color: #999;")
                dismiss_btn.clicked.connect(
                    lambda _, eid=eid: self._on_dismiss(eid)
                )
                al.addWidget(dismiss_btn)
            elif status == "sent":
                done = QLabel("✅")
                done.setAlignment(Qt.AlignmentFlag.AlignCenter)
                al.addWidget(done)
            elif status == "dismissed":
                done = QLabel("—")
                done.setAlignment(Qt.AlignmentFlag.AlignCenter)
                al.addWidget(done)

            self._log_table.setCellWidget(row, 7, actions)

        self._kpi_pending.setText(str(pending_count))
        self._kpi_sent.setText(str(sent_today))

    def _on_send_suggestion(self, log_id: int) -> None:
        entries = get_automation_log(status="suggested")
        entry = None
        for e in entries:
            if e["id"] == log_id:
                entry = e
                break
        if not entry:
            QMessageBox.warning(self, "Not Found", "Suggestion not found or already handled.")
            self._load_suggestions()
            return

        phone = entry.get("phone", "")
        message = entry.get("message", "")
        cust_name = entry.get("customer_name", "Customer")

        if not phone:
            QMessageBox.warning(
                self, "No Phone",
                f"{cust_name} has no phone number on file."
            )
            return

        if not message:
            # Let user compose
            dlg = QDialog(self)
            dlg.setWindowTitle(f"Send SMS to {cust_name}")
            dlg.setMinimumWidth(400)
            fl = QFormLayout(dlg)
            fl.addRow("To:", QLabel(f"{cust_name} — {phone}"))
            msg_edit = QTextEdit()
            msg_edit.setPlaceholderText(f"Hey {cust_name}, ...")
            msg_edit.setMaximumHeight(100)
            fl.addRow("Message:", msg_edit)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(dlg.accept)
            buttons.rejected.connect(dlg.reject)
            fl.addRow(buttons)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            message = msg_edit.toPlainText().strip()
            if not message:
                return

        if not sms_configured():
            QMessageBox.warning(
                self, "SMS Not Configured",
                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and "
                "TWILIO_PHONE_NUMBER in your .env file."
            )
            return

        try:
            if _send_sms:
                _send_sms(phone, message)
            update_automation_log_status(log_id, "sent")
            self._load_suggestions()
        except Exception as exc:
            log.warning("Automation SMS failed for %s: %s", cust_name, exc)
            update_automation_log_status(log_id, "failed")
            self._load_suggestions()
            QMessageBox.warning(
                self, "Send Failed",
                f"Could not send SMS to {cust_name}: {exc}"
            )

    def _on_dismiss(self, log_id: int) -> None:
        update_automation_log_status(log_id, "dismissed")
        self._load_suggestions()

    # ==================================================================
    #  KPIs
    # ==================================================================
    def _refresh_kpis(self) -> None:
        try:
            rules = get_automation_rules()
            self._kpi_active.setText(str(sum(1 for r in rules if r.get("is_active"))))
        except Exception:
            pass
        try:
            pending = get_automation_log(status="suggested")
            self._kpi_pending.setText(str(len(pending)))
        except Exception:
            pass
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            sent = get_automation_log(status="sent")
            self._kpi_sent.setText(str(sum(
                1 for s in sent if (s.get("acted_at") or "").startswith(today)
            )))
        except Exception:
            pass

    def refresh(self) -> None:
        self._refresh_kpis()
        self._load_rules()
        self._load_suggestions()
