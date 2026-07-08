"""CRM Activity Screen - Customer visit notes, calls, follow-ups, reminders."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QDate

from db import (
    add_customer_activity,
    get_customer_activities,
    get_all_activities,
    get_upcoming_followups,
    complete_activity,
    delete_activity,
    get_all_customers,
    search_customers,
)


ACTIVITY_TYPES = ["visit", "call", "email", "note", "follow-up", "quote", "complaint"]

ACTIVITY_COLORS = {
    "visit": "#4CAF50",
    "call": "#2196F3",
    "email": "#9C27B0",
    "note": "#607D8B",
    "follow-up": "#FF9800",
    "quote": "#00BCD4",
    "complaint": "#F44336",
}


class _KPICard(QFrame):
    def __init__(self, title: str, color: str = "#3d4a38", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"background: {color}; border-radius: 6px; padding: 8px;")
        self._title = QLabel(title)
        self._title.setStyleSheet("color: white; font-size: 10pt;")
        self._value = QLabel("0")
        self._value.setStyleSheet("color: white; font-size: 22pt; font-weight: bold;")
        self._sub = QLabel("")
        self._sub.setStyleSheet("color: #ccc; font-size: 9pt;")
        lay = QVBoxLayout()
        lay.addWidget(self._title)
        lay.addWidget(self._value)
        lay.addWidget(self._sub)
        self.setLayout(lay)

    def set_value(self, val: str, sub: str = ""):
        self._value.setText(val)
        self._sub.setText(sub)


class _ActivityDialog(QDialog):
    """Dialog to log a new customer activity."""

    def __init__(self, customers: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Log Activity")
        self.setMinimumWidth(480)

        form = QFormLayout()

        self.customer_combo = QComboBox()
        self.customer_combo.setEditable(True)
        for c in customers:
            label = str(c.get("name", ""))
            company = c.get("company", "")
            if company:
                label += f" ({company})"
            self.customer_combo.addItem(label, c.get("id", 0))
        form.addRow("Customer:", self.customer_combo)

        self.type_combo = QComboBox()
        self.type_combo.addItems(ACTIVITY_TYPES)
        form.addRow("Type:", self.type_combo)

        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("Brief subject line...")
        form.addRow("Subject:", self.subject_edit)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Detailed notes about the interaction...")
        self.notes_edit.setMaximumHeight(120)
        form.addRow("Notes:", self.notes_edit)

        self.followup_cb = QComboBox()
        self.followup_cb.addItems(["No follow-up", "In 3 days", "In 1 week",
                                   "In 2 weeks", "In 1 month", "Custom date"])
        self.followup_cb.currentIndexChanged.connect(self._on_followup_changed)
        form.addRow("Follow-up:", self.followup_cb)

        self.followup_date = QDateEdit()
        self.followup_date.setCalendarPopup(True)
        self.followup_date.setDate(QDate.currentDate().addDays(7))
        self.followup_date.setVisible(False)
        form.addRow("", self.followup_date)

        btns = QHBoxLayout()
        ok_btn = QPushButton("Save")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

    def _on_followup_changed(self, idx):
        self.followup_date.setVisible(idx == 5)  # "Custom date"

    def get_followup_date(self) -> str | None:
        idx = self.followup_cb.currentIndex()
        if idx == 0:
            return None
        today = QDate.currentDate()
        offsets = {1: 3, 2: 7, 3: 14, 4: 30}
        if idx in offsets:
            return today.addDays(offsets[idx]).toString("yyyy-MM-dd")
        if idx == 5:
            return self.followup_date.date().toString("yyyy-MM-dd")
        return None


class CRMScreen(QWidget):
    """CRM activity log, customer visit notes, and follow-up tracking."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._activities: list[dict] = []

        layout = QVBoxLayout()

        # KPI cards
        self._card_total = _KPICard("Total Activities", "#3d4a38")
        self._card_visits = _KPICard("Visits", "#4CAF50")
        self._card_calls = _KPICard("Calls", "#2196F3")
        self._card_followups = _KPICard("Open Follow-ups", "#FF9800")
        self._card_overdue = _KPICard("Overdue", "#b22222")
        cards = QHBoxLayout()
        for c in [self._card_total, self._card_visits, self._card_calls,
                  self._card_followups, self._card_overdue]:
            cards.addWidget(c)
        layout.addLayout(cards)

        # Toolbar
        toolbar = QHBoxLayout()
        self._filter_type = QComboBox()
        self._filter_type.addItem("All Types", "")
        for t in ACTIVITY_TYPES:
            self._filter_type.addItem(t.title(), t)
        self._filter_type.currentIndexChanged.connect(self._refresh)

        self._filter_customer = QLineEdit()
        self._filter_customer.setPlaceholderText("Filter by customer...")
        self._filter_customer.setMaximumWidth(200)
        self._filter_customer.returnPressed.connect(self._refresh)

        new_btn = QPushButton("➕ Log Activity")
        new_btn.clicked.connect(self._new_activity)
        followup_btn = QPushButton("📅 Follow-ups Due")
        followup_btn.clicked.connect(self._show_followups)
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self._refresh)

        toolbar.addWidget(QLabel("Type:"))
        toolbar.addWidget(self._filter_type)
        toolbar.addWidget(self._filter_customer)
        toolbar.addWidget(new_btn)
        toolbar.addWidget(followup_btn)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Activities table
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["ID", "Date", "Customer", "Type", "Subject", "Follow-up", "Done"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnHidden(0, True)
        self._table.cellDoubleClicked.connect(self._view_detail)
        layout.addWidget(self._table, 1)

        # Action buttons
        action_row = QHBoxLayout()
        complete_btn = QPushButton("✅ Mark Complete")
        complete_btn.clicked.connect(self._mark_complete)
        delete_btn = QPushButton("🗑 Delete")
        delete_btn.clicked.connect(self._delete_activity)
        action_row.addWidget(complete_btn)
        action_row.addWidget(delete_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.setLayout(layout)
        self._refresh()

    def _refresh(self):
        atype = self._filter_type.currentData() or None
        try:
            self._activities = get_all_activities(activity_type=atype)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            self._activities = []

        # Filter by customer name if entered
        cust_filter = self._filter_customer.text().strip().lower()
        if cust_filter:
            self._activities = [
                a for a in self._activities
                if cust_filter in str(a.get("customer_name", "")).lower()
                or cust_filter in str(a.get("company", "")).lower()
            ]

        self._populate_table()
        self._update_kpis()

    def _populate_table(self):
        self._table.setRowCount(len(self._activities))
        for i, a in enumerate(self._activities):
            atype = str(a.get("activity_type", ""))
            color = ACTIVITY_COLORS.get(atype, "#666")

            def item(text, bg=None):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if bg:
                    from PySide6.QtGui import QColor
                    it.setForeground(QColor(bg))
                return it

            self._table.setItem(i, 0, item(a.get("id", "")))
            date_str = str(a.get("created_at", ""))[:16]
            self._table.setItem(i, 1, item(date_str))
            cust = str(a.get("customer_name", ""))
            company = a.get("company", "")
            if company:
                cust += f" ({company})"
            self._table.setItem(i, 2, item(cust))
            self._table.setItem(i, 3, item(atype.upper(), color))
            self._table.setItem(i, 4, item(a.get("subject", "")))
            followup = a.get("follow_up_date", "") or ""
            self._table.setItem(i, 5, item(followup))
            done = "✅" if a.get("completed") else ""
            self._table.setItem(i, 6, item(done))

    def _update_kpis(self):
        total = len(self._activities)
        visits = sum(1 for a in self._activities if a.get("activity_type") == "visit")
        calls = sum(1 for a in self._activities if a.get("activity_type") == "call")
        self._card_total.set_value(str(total))
        self._card_visits.set_value(str(visits))
        self._card_calls.set_value(str(calls))

        try:
            followups = get_upcoming_followups(365)
            overdue = get_upcoming_followups(0)
            self._card_followups.set_value(str(len(followups)), "pending")
            self._card_overdue.set_value(str(len(overdue)), "past due")
        except Exception:
            pass

    def _new_activity(self):
        try:
            customers = get_all_customers()
        except Exception:
            customers = []
        if not customers:
            QMessageBox.information(self, "Info", "Add customers first.")
            return
        dlg = _ActivityDialog(customers, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cid = dlg.customer_combo.currentData()
        atype = dlg.type_combo.currentText()
        subject = dlg.subject_edit.text().strip()
        notes = dlg.notes_edit.toPlainText().strip()
        followup = dlg.get_followup_date()

        if not subject:
            QMessageBox.warning(self, "Missing", "Subject is required.")
            return
        try:
            add_customer_activity(cid, atype, subject, notes=notes or None,
                                  follow_up_date=followup)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _view_detail(self, row: int, _col: int):
        if row < 0 or row >= len(self._activities):
            return
        a = self._activities[row]
        detail = (
            f"Date: {a.get('created_at', '')}\n"
            f"Customer: {a.get('customer_name', '')} {a.get('company', '') or ''}\n"
            f"Type: {a.get('activity_type', '')}\n"
            f"Subject: {a.get('subject', '')}\n"
            f"Follow-up: {a.get('follow_up_date', '') or 'None'}\n"
            f"Completed: {'Yes' if a.get('completed') else 'No'}\n\n"
            f"Notes:\n{a.get('notes', '') or '(none)'}"
        )
        QMessageBox.information(self, "Activity Detail", detail)

    def _mark_complete(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._activities):
            return
        aid = self._activities[row].get("id")
        try:
            complete_activity(aid)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_activity(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._activities):
            return
        if QMessageBox.question(self, "Delete", "Delete this activity?") != QMessageBox.StandardButton.Yes:
            return
        aid = self._activities[row].get("id")
        try:
            delete_activity(aid)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _show_followups(self):
        try:
            followups = get_upcoming_followups(14)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if not followups:
            QMessageBox.information(self, "Follow-ups", "No follow-ups due in the next 14 days.")
            return
        lines = ["=== FOLLOW-UPS DUE (next 14 days) ===\n"]
        for f in followups:
            lines.append(
                f"📅 {f.get('follow_up_date', '')}  |  "
                f"{f.get('customer_name', '')}  |  "
                f"{f.get('subject', '')}\n"
                f"   Phone: {f.get('phone', '') or 'N/A'}  "
                f"Email: {f.get('email', '') or 'N/A'}")
        QMessageBox.information(self, "Upcoming Follow-ups", "\n".join(lines))
