"""Two-way text messaging screen — iMessage-style chat UI.

Left panel: conversation list (customers with messages).
Right panel: chat thread with message bubbles + compose bar.
"""
from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QBrush, QPen, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import get_all_customers, get_sms_templates
from db.database import get_db

log = logging.getLogger(__name__)


# ── Bubble colours ──────────────────────────────────────────────────
_OUTBOUND_BG = "#6B8E23"   # Olive green (our messages)
_OUTBOUND_FG = "#FFFFFF"
_INBOUND_BG = "#3A3F47"    # Dark grey (their replies)
_INBOUND_FG = "#FFFFFF"
_TIME_FG = "#999999"


# ====================================================================
#  Chat bubble widget
# ====================================================================
class _ChatBubble(QWidget):
    """A single message bubble."""

    def __init__(self, text: str, timestamp: str, outbound: bool, parent=None):
        super().__init__(parent)
        self._outbound = outbound

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 2, 8, 2)

        # Push bubble to right (outbound) or left (inbound)
        if outbound:
            outer.addStretch()

        bubble = QWidget()
        bubble.setMaximumWidth(420)
        bubble.setStyleSheet(f"""
            QWidget {{
                background-color: {_OUTBOUND_BG if outbound else _INBOUND_BG};
                border-radius: 12px;
                padding: 8px 12px;
            }}
        """)
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(10, 6, 10, 4)
        bl.setSpacing(2)

        msg_label = QLabel(text)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet(f"color: {_OUTBOUND_FG if outbound else _INBOUND_FG}; font-size: 10pt; background: transparent;")
        bl.addWidget(msg_label)

        time_label = QLabel(timestamp)
        time_label.setStyleSheet(f"color: {_TIME_FG}; font-size: 8pt; background: transparent;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignRight if outbound else Qt.AlignmentFlag.AlignLeft)
        bl.addWidget(time_label)

        outer.addWidget(bubble)

        if not outbound:
            outer.addStretch()


# ====================================================================
#  Conversation list item
# ====================================================================
class _ConvoItem(QListWidgetItem):
    """Customer conversation entry for the left panel."""

    def __init__(self, customer_id: int, name: str, phone: str,
                 last_msg: str = "", last_time: str = "", unread: int = 0):
        display = name or phone
        if unread > 0:
            display = f"({unread}) {display}"
        super().__init__(display)
        self.customer_id = customer_id
        self.customer_name = name
        self.phone = phone
        self.last_msg = last_msg
        self.last_time = last_time
        self.unread = unread

        if unread > 0:
            f = self.font()
            f.setBold(True)
            self.setFont(f)


# ====================================================================
#  Main screen
# ====================================================================
class MessagingScreen(QWidget):
    """Two-way SMS/text messaging screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_customer_id: int | None = None
        self._current_phone: str = ""
        self._build_ui()
        self._load_conversations()

        # Auto-refresh every 15 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_auto_refresh)
        self._refresh_timer.start(15_000)

    # ------------------------------------------------------------------
    #  UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- Left: conversation list ----
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)

        # Header
        hdr = QLabel("  💬  Conversations")
        hdr.setStyleSheet("background: #3d4a38; color: white; font-size: 12pt; font-weight: bold; padding: 10px;")
        ll.addWidget(hdr)

        # Search contacts
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 Search customer…")
        self._search.setStyleSheet("padding: 6px; font-size: 10pt; margin: 4px;")
        self._search.textChanged.connect(self._on_search)
        ll.addWidget(self._search)

        # New conversation button
        self._btn_new = QPushButton("➕  New Conversation")
        self._btn_new.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_new.setStyleSheet("""
            QPushButton {
                background-color: #6B8E23; color: white; font-weight: bold;
                padding: 8px; border: none; margin: 4px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #7FA02E; }
        """)
        self._btn_new.clicked.connect(self._on_new_conversation)
        ll.addWidget(self._btn_new)

        # Conversation list
        self._convo_list = QListWidget()
        self._convo_list.setStyleSheet("""
            QListWidget {
                background: #2b2b2b; color: white; border: none; font-size: 10pt;
            }
            QListWidget::item {
                padding: 10px 8px;
                border-bottom: 1px solid #3a3a3a;
            }
            QListWidget::item:selected {
                background-color: #4d5a48;
            }
            QListWidget::item:hover:!selected {
                background-color: #333333;
            }
        """)
        self._convo_list.currentItemChanged.connect(self._on_conversation_selected)
        ll.addWidget(self._convo_list, 1)

        splitter.addWidget(left)

        # ---- Right: chat panel ----
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # Chat header
        self._chat_header = QLabel("  Select a conversation")
        self._chat_header.setStyleSheet(
            "background: #3d4a38; color: white; font-size: 12pt; font-weight: bold; padding: 10px;"
        )
        rl.addWidget(self._chat_header)

        # Scroll area for bubbles
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { background: #1e1e1e; border: none; }")

        self._bubble_container = QWidget()
        self._bubble_layout = QVBoxLayout(self._bubble_container)
        self._bubble_layout.setContentsMargins(4, 8, 4, 8)
        self._bubble_layout.setSpacing(4)
        self._bubble_layout.addStretch()
        self._scroll.setWidget(self._bubble_container)
        rl.addWidget(self._scroll, 1)

        # Compose bar
        compose = QWidget()
        compose.setStyleSheet("background: #2b2b2b;")
        cl = QHBoxLayout(compose)
        cl.setContentsMargins(8, 6, 8, 6)

        # Template insert button
        self._btn_template = QPushButton("📝")
        self._btn_template.setToolTip("Insert template")
        self._btn_template.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_template.setFixedSize(36, 36)
        self._btn_template.setStyleSheet(
            "QPushButton { background: #3a3a3a; color: white; border: 1px solid #555; "
            "border-radius: 6px; font-size: 14pt; }"
            "QPushButton:hover { background: #4a4a4a; }"
        )
        self._btn_template.clicked.connect(self._on_insert_template)
        cl.addWidget(self._btn_template)

        self._msg_input = QTextEdit()
        self._msg_input.setPlaceholderText("Type a message…")
        self._msg_input.setMaximumHeight(80)
        self._msg_input.setStyleSheet("""
            QTextEdit {
                background: #3a3a3a; color: white; border: 1px solid #555;
                border-radius: 8px; padding: 6px; font-size: 10pt;
            }
        """)
        cl.addWidget(self._msg_input, 1)

        self._btn_send = QPushButton("Send ➤")
        self._btn_send.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_send.setStyleSheet("""
            QPushButton {
                background-color: #6B8E23; color: white; font-weight: bold;
                font-size: 11pt; padding: 10px 20px; border: none; border-radius: 8px;
            }
            QPushButton:hover { background-color: #7FA02E; }
            QPushButton:disabled { background-color: #555; color: #888; }
        """)
        self._btn_send.setEnabled(False)
        self._btn_send.clicked.connect(self._on_send)
        cl.addWidget(self._btn_send)

        rl.addWidget(compose)

        splitter.addWidget(right)

        # ---- Far-right: customer summary panel ----
        self._summary_panel = QWidget()
        self._summary_panel.setFixedWidth(260)
        self._summary_panel.setStyleSheet("background: #2b2b2b; border-left: 1px solid #3a3a3a;")
        sp_lay = QVBoxLayout(self._summary_panel)
        sp_lay.setContentsMargins(10, 10, 10, 10)
        sp_lay.setSpacing(6)

        sp_title = QLabel("👤 Customer")
        sp_title.setStyleSheet("font-size: 11pt; font-weight: bold; color: white;")
        sp_lay.addWidget(sp_title)

        self._sp_name = QLabel("—")
        self._sp_name.setStyleSheet("font-size: 10pt; color: white; font-weight: bold;")
        self._sp_name.setWordWrap(True)
        sp_lay.addWidget(self._sp_name)

        self._sp_company = QLabel("")
        self._sp_company.setStyleSheet("font-size: 9pt; color: #aaa;")
        self._sp_company.setWordWrap(True)
        sp_lay.addWidget(self._sp_company)

        self._sp_phone = QLabel("")
        self._sp_phone.setStyleSheet("font-size: 9pt; color: #ccc;")
        sp_lay.addWidget(self._sp_phone)

        self._sp_email = QLabel("")
        self._sp_email.setStyleSheet("font-size: 9pt; color: #ccc;")
        self._sp_email.setWordWrap(True)
        sp_lay.addWidget(self._sp_email)

        self._sp_type = QLabel("")
        self._sp_type.setStyleSheet("font-size: 9pt; color: #6B8E23;")
        sp_lay.addWidget(self._sp_type)

        self._sp_balance = QLabel("")
        self._sp_balance.setStyleSheet("font-size: 9pt; color: #FFA726;")
        sp_lay.addWidget(self._sp_balance)

        sep = QLabel("")
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #3a3a3a;")
        sp_lay.addWidget(sep)

        # Quick actions
        qa_label = QLabel("⚡ Quick Actions")
        qa_label.setStyleSheet("font-size: 9pt; font-weight: bold; color: #999; margin-top: 4px;")
        sp_lay.addWidget(qa_label)

        self._btn_create_quote = QPushButton("📋 Create Quote")
        self._btn_create_quote.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_create_quote.setStyleSheet(
            "QPushButton { background: #3a3a3a; color: white; border: 1px solid #555; "
            "border-radius: 4px; padding: 6px; font-size: 9pt; text-align: left; }"
            "QPushButton:hover { background: #4a4a4a; }"
        )
        self._btn_create_quote.clicked.connect(self._on_create_quote)
        sp_lay.addWidget(self._btn_create_quote)

        self._btn_view_profile = QPushButton("👤 View Profile")
        self._btn_view_profile.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_view_profile.setStyleSheet(
            "QPushButton { background: #3a3a3a; color: white; border: 1px solid #555; "
            "border-radius: 4px; padding: 6px; font-size: 9pt; text-align: left; }"
            "QPushButton:hover { background: #4a4a4a; }"
        )
        self._btn_view_profile.clicked.connect(self._on_view_profile)
        sp_lay.addWidget(self._btn_view_profile)

        self._btn_view_invoices = QPushButton("💰 View Invoices")
        self._btn_view_invoices.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_view_invoices.setStyleSheet(
            "QPushButton { background: #3a3a3a; color: white; border: 1px solid #555; "
            "border-radius: 4px; padding: 6px; font-size: 9pt; text-align: left; }"
            "QPushButton:hover { background: #4a4a4a; }"
        )
        self._btn_view_invoices.clicked.connect(self._on_view_invoices)
        sp_lay.addWidget(self._btn_view_invoices)

        sp_lay.addStretch()
        self._summary_panel.setVisible(False)
        splitter.addWidget(self._summary_panel)

        splitter.setStretchFactor(0, 1)   # left  ~25%
        splitter.setStretchFactor(1, 3)   # right ~55%
        splitter.setStretchFactor(2, 0)   # summary panel fixed width
        splitter.setSizes([280, 700, 260])

        root.addWidget(splitter)

    # ------------------------------------------------------------------
    #  Data helpers
    # ------------------------------------------------------------------
    def _get_conversations(self) -> list[dict]:
        """Get customers that have messages, ordered by most recent."""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT
                    m.customer_id,
                    c.name,
                    m.phone,
                    m.body  AS last_msg,
                    m.created_at AS last_time,
                    (SELECT COUNT(*) FROM messages m2
                     WHERE m2.customer_id = m.customer_id
                       AND m2.direction = 'inbound'
                       AND m2.read = 0) AS unread
                FROM messages m
                INNER JOIN customers c ON c.id = m.customer_id
                WHERE m.id = (
                    SELECT MAX(m3.id) FROM messages m3
                    WHERE m3.customer_id = m.customer_id
                )
                ORDER BY m.created_at DESC
            """).fetchall()
            return [dict(r) if hasattr(r, "keys") else {} for r in rows]

    def _get_thread(self, customer_id: int) -> list[dict]:
        """Get all messages for a customer, oldest first."""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM messages
                WHERE customer_id = ?
                ORDER BY created_at ASC
            """, (customer_id,)).fetchall()
            return [dict(r) if hasattr(r, "keys") else {} for r in rows]

    def _mark_read(self, customer_id: int):
        """Mark all inbound messages for this customer as read."""
        with get_db() as conn:
            conn.execute("""
                UPDATE messages SET read = 1
                WHERE customer_id = ? AND direction = 'inbound' AND read = 0
            """, (customer_id,))
            conn.commit()

    def _save_message(self, customer_id: int, phone: str, direction: str,
                      body: str, twilio_sid: str = "", status: str = "sent") -> int:
        """Insert a message and return its id."""
        with get_db() as conn:
            cur = conn.execute("""
                INSERT INTO messages (customer_id, phone, direction, body, twilio_sid, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (customer_id, phone, direction, body, twilio_sid, status))
            conn.commit()
            return cur.lastrowid

    # ------------------------------------------------------------------
    #  Load / refresh
    # ------------------------------------------------------------------
    def _load_conversations(self):
        """Populate or refresh the conversation list."""
        selected_cid = self._current_customer_id
        self._convo_list.blockSignals(True)
        self._convo_list.clear()

        convos = self._get_conversations()
        select_idx = -1
        for i, c in enumerate(convos):
            item = _ConvoItem(
                customer_id=c["customer_id"],
                name=c.get("name", ""),
                phone=c.get("phone", ""),
                last_msg=c.get("last_msg", ""),
                last_time=c.get("last_time", ""),
                unread=c.get("unread", 0),
            )
            # Tooltip: last message preview
            preview = (c.get("last_msg") or "")[:60]
            ts = c.get("last_time", "")
            item.setToolTip(f"{ts}\n{preview}")
            self._convo_list.addItem(item)
            if c["customer_id"] == selected_cid:
                select_idx = i

        self._convo_list.blockSignals(False)
        if select_idx >= 0:
            self._convo_list.setCurrentRow(select_idx)

    def _load_thread(self, customer_id: int):
        """Render the chat bubbles for this customer."""
        # Clear existing bubbles
        while self._bubble_layout.count() > 1:
            child = self._bubble_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        msgs = self._get_thread(customer_id)
        for m in msgs:
            ts_raw = m.get("created_at", "")
            try:
                dt = datetime.fromisoformat(ts_raw)
                ts = dt.strftime("%b %d, %I:%M %p")
            except Exception:
                ts = ts_raw

            outbound = m.get("direction", "outbound") == "outbound"
            bubble = _ChatBubble(m.get("body", ""), ts, outbound)
            # Insert before the stretch at the end
            self._bubble_layout.insertWidget(self._bubble_layout.count() - 1, bubble)

        # Mark inbound as read
        self._mark_read(customer_id)

        # Scroll to bottom
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------------------
    #  Slots
    # ------------------------------------------------------------------
    def _on_conversation_selected(self, current, previous):
        if not current or not isinstance(current, _ConvoItem):
            return
        self._current_customer_id = current.customer_id
        self._current_phone = current.phone
        self._chat_header.setText(f"  💬  {current.customer_name or current.phone}")
        self._btn_send.setEnabled(True)
        self._load_thread(current.customer_id)
        self._update_summary_panel(current.customer_id)

    def _on_search(self, text: str):
        text_lower = text.lower()
        for i in range(self._convo_list.count()):
            item = self._convo_list.item(i)
            if isinstance(item, _ConvoItem):
                visible = (text_lower in (item.customer_name or "").lower()
                           or text_lower in (item.phone or ""))
                item.setHidden(not visible)

    def _on_new_conversation(self):
        """Pick a customer to start a new conversation with."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QComboBox, QFormLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("New Conversation")
        dlg.setMinimumWidth(350)
        fl = QFormLayout(dlg)

        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        customers = get_all_customers()
        customer_map = {}
        for c in customers:
            phone = c.get("phone", "")
            name = c.get("name", "")
            cid = c.get("id")
            if phone:
                label = f"{name} — {phone}" if name else phone
                combo.addItem(label)
                customer_map[label] = (cid, name, phone)

        fl.addRow("Customer:", combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        fl.addRow(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        chosen = combo.currentText()
        if chosen not in customer_map:
            QMessageBox.warning(self, "Invalid", "Please select a customer with a phone number.")
            return

        cid, name, phone = customer_map[chosen]
        self._current_customer_id = cid
        self._current_phone = phone
        self._chat_header.setText(f"  💬  {name or phone}")
        self._btn_send.setEnabled(True)
        self._load_thread(cid)
        self._load_conversations()  # refresh list to show/select

    def _on_send(self):
        """Send a message to the current customer."""
        body = self._msg_input.toPlainText().strip()
        if not body or not self._current_customer_id:
            return

        from .sms_service_bridge import send_sms_message

        phone = self._current_phone
        result = send_sms_message(phone, body)

        twilio_sid = result.get("twilio_sid", "")
        status = "sent" if result.get("success") else "failed"

        # Save to DB regardless
        self._save_message(
            self._current_customer_id, phone, "outbound",
            body, twilio_sid, status,
        )

        if not result.get("success"):
            # Still show in chat but flag it
            log.warning("SMS send failed: %s", result.get("error", ""))

        # Clear input and refresh thread
        self._msg_input.clear()
        self._load_thread(self._current_customer_id)
        self._load_conversations()

    def _on_auto_refresh(self):
        """Poll for new inbound messages."""
        if not self.isVisible():
            return
        self._load_conversations()
        if self._current_customer_id:
            self._load_thread(self._current_customer_id)

    # ------------------------------------------------------------------
    #  Customer summary panel
    # ------------------------------------------------------------------
    def _update_summary_panel(self, customer_id: int) -> None:
        """Populate the right-side summary panel for the selected customer."""
        self._summary_panel.setVisible(True)
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
        if not row:
            self._sp_name.setText("—")
            self._sp_company.setText("")
            self._sp_phone.setText("")
            self._sp_email.setText("")
            self._sp_type.setText("")
            self._sp_balance.setText("")
            return
        c = dict(row) if hasattr(row, "keys") else {}
        self._sp_name.setText(c.get("name", "—"))
        self._sp_company.setText(c.get("company", "") or "")
        self._sp_phone.setText(f"📱 {c.get('phone', '')}" if c.get("phone") else "")
        self._sp_email.setText(f"📧 {c.get('email', '')}" if c.get("email") else "")
        self._sp_type.setText(f"Type: {c.get('customer_type', 'N/A')}")
        balance = c.get("outstanding_balance") or c.get("balance") or 0
        if balance:
            self._sp_balance.setText(f"Balance: ${balance:,.2f}")
        else:
            self._sp_balance.setText("")

    # ------------------------------------------------------------------
    #  Quick actions
    # ------------------------------------------------------------------
    def _on_create_quote(self) -> None:
        if not self._current_customer_id:
            return
        # Navigate to quotes screen with this customer pre-selected
        mw = self.window()
        if hasattr(mw, "navigate_to_screen"):
            mw.navigate_to_screen("Quotes", customer_id=self._current_customer_id)
        else:
            QMessageBox.information(
                self, "Create Quote",
                f"Navigate to Quotes and create a new quote for customer #{self._current_customer_id}."
            )

    def _on_view_profile(self) -> None:
        if not self._current_customer_id:
            return
        mw = self.window()
        if hasattr(mw, "navigate_to_screen"):
            mw.navigate_to_screen("Customer Maintenance", customer_id=self._current_customer_id)
        else:
            QMessageBox.information(
                self, "View Profile",
                f"Navigate to Customer Maintenance for customer #{self._current_customer_id}."
            )

    def _on_view_invoices(self) -> None:
        if not self._current_customer_id:
            return
        mw = self.window()
        if hasattr(mw, "navigate_to_screen"):
            mw.navigate_to_screen("Invoices", customer_id=self._current_customer_id)
        else:
            QMessageBox.information(
                self, "View Invoices",
                f"Navigate to Invoices for customer #{self._current_customer_id}."
            )

    # ------------------------------------------------------------------
    #  Template insertion
    # ------------------------------------------------------------------
    def _on_insert_template(self) -> None:
        """Show a template picker and insert selected template into compose bar."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QComboBox, QFormLayout

        templates = get_sms_templates()
        if not templates:
            QMessageBox.information(
                self, "No Templates",
                "No templates found. Create templates in SMS Campaigns → Templates."
            )
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Insert Template")
        dlg.setMinimumWidth(400)
        fl = QFormLayout(dlg)

        combo = QComboBox()
        for t in templates:
            combo.addItem(f"[{t['category']}] {t['name']}", t["id"])
        fl.addRow("Template:", combo)

        preview = QLabel("")
        preview.setWordWrap(True)
        preview.setStyleSheet("color: #ccc; font-size: 9pt; padding: 4px;")

        def _update_preview(idx):
            tid = combo.currentData()
            for t in templates:
                if t["id"] == tid:
                    preview.setText(t["body"][:200])
                    break
        combo.currentIndexChanged.connect(_update_preview)
        _update_preview(0)
        fl.addRow("Preview:", preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        fl.addRow(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        tid = combo.currentData()
        for t in templates:
            if t["id"] == tid:
                body = t["body"]
                # Auto-substitute {name} if we know the customer
                if self._current_customer_id:
                    item = self._convo_list.currentItem()
                    if isinstance(item, _ConvoItem) and item.customer_name:
                        body = body.replace("{name}", item.customer_name)
                self._msg_input.setPlainText(body)
                break
