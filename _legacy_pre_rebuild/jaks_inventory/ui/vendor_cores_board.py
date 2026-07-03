"""Vendor Cores Board — Phase G kanban view.

Five columns matching ``VCO_LIFECYCLE``. Each card is one obligation
(or one RGA, in the Shipped column). Most state transitions are driven
from this screen via context-menu actions; the Pending Ship → Shipped
transition is gated through ``PackAndShipDialog`` which forces a
packing slip + freight label PDF to exist first.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from db.core_handling import (
    VCO_LIFECYCLE,
    fold_status,
    list_vendor_core_obligations,
    transition_to_cancelled,
    transition_to_credit_received,
    transition_to_write_off,
)


_COLUMN_TIPS = {
    "on_shelf":               "Cores we received from this vendor's PO; still on the shelf.",
    "awaiting_customer_core": "Sold to a customer; waiting on their core return.",
    "pending_vendor_ship":    "Customer core in hand; ready to box up & ship to vendor.",
    "shipped":                "Boxed and shipped to vendor; awaiting their credit.",
    "credit_received":        "Vendor issued credit. \U0001F4B0 Closed.",
}


class _CoreCard(QListWidgetItem):
    def __init__(self, obligation: dict):
        super().__init__()
        self._obl = obligation
        self.setData(Qt.ItemDataRole.UserRole, int(obligation.get("id") or 0))
        sku = obligation.get("product_sku") or ""
        title = obligation.get("product_title") or ""
        po = obligation.get("po_number") or ""
        rga = obligation.get("rga_number") or ""
        deposit = float(obligation.get("deposit_each") or 0)
        units = int(obligation.get("units") or 1)
        ref = po or rga or ""
        ref_label = "PO" if po and not rga else ("RGA" if rga else "")
        lines = [
            f"<b>{sku}</b>" if sku else "<i>(no SKU)</i>",
            title,
        ]
        if ref:
            lines.append(f"<font color='#666'>{ref_label} {ref}</font>")
        if deposit > 0:
            lines.append(f"<font color='#444'>${deposit:,.2f} × {units}</font>")
        self.setText(_strip_html_for_list("\n".join(lines)))
        self.setToolTip("\n".join([f"#{obligation.get('id')}",
                                   title, f"PO: {po}", f"RGA: {rga}"]))


def _strip_html_for_list(text: str) -> str:
    """QListWidget items don't render HTML; flatten gracefully."""
    import re
    return re.sub(r"<[^>]+>", "", text)


class _Column(QFrame):
    """One kanban column. Pure UI; transitions are owned by the board."""

    card_double_clicked = Signal(int)        # obligation_id
    context_action_requested = Signal(str, int)  # action_key, obligation_id

    def __init__(self, status: str, label: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._status = status
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: #f3f3ef; border-radius: 6px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        hdr = QLabel(f"<b>{label}</b>")
        hdr.setStyleSheet("font-size: 11pt;")
        lay.addWidget(hdr)
        self._count = QLabel("0")
        self._count.setStyleSheet("color: #666; font-size: 9pt;")
        lay.addWidget(self._count)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #ddd; }"
            "QListWidget::item { padding: 6px; border-bottom: 1px solid #eee; }"
        )
        self._list.setWordWrap(True)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.setToolTip(_COLUMN_TIPS.get(status, ""))
        lay.addWidget(self._list, 1)

    @property
    def status(self) -> str:
        return self._status

    def set_cards(self, obligations: list[dict]) -> None:
        self._list.clear()
        for o in obligations:
            self._list.addItem(_CoreCard(o))
        self._count.setText(f"{len(obligations)} card(s)")

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        obl_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if obl_id:
            self.card_double_clicked.emit(obl_id)

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if not item:
            return
        obl_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        menu = QMenu(self)
        # Actions vary by column.
        if self._status == "on_shelf":
            menu.addAction("Reassign to customer core…",
                           lambda: self.context_action_requested.emit("reassign", obl_id))
            menu.addAction("Cancel obligation…",
                           lambda: self.context_action_requested.emit("cancel", obl_id))
        elif self._status == "awaiting_customer_core":
            menu.addAction("Mark customer core received",
                           lambda: self.context_action_requested.emit("mark_received", obl_id))
            menu.addAction("Write off (customer never returned)…",
                           lambda: self.context_action_requested.emit("write_off", obl_id))
        elif self._status == "pending_vendor_ship":
            menu.addAction("Open Pack & Ship dialog…",
                           lambda: self.context_action_requested.emit("pack_and_ship", obl_id))
            menu.addAction("Cancel obligation…",
                           lambda: self.context_action_requested.emit("cancel", obl_id))
        elif self._status == "shipped":
            menu.addAction("Mark credit received…",
                           lambda: self.context_action_requested.emit("credit_received", obl_id))
            menu.addAction("Vendor rejected (write off)…",
                           lambda: self.context_action_requested.emit("write_off", obl_id))
        elif self._status == "credit_received":
            menu.addAction("View RGA details",
                           lambda: self.context_action_requested.emit("view", obl_id))
        if menu.actions():
            menu.exec(QCursor.pos())


class VendorCoresBoard(QWidget):
    """Kanban for vendor core obligations.

    Use ``load(vendor_id=..., focus_status=...)`` to scope the board.
    Emits ``back_to_overview`` when the breadcrumb is clicked.
    """

    back_to_overview = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._vendor_id: Optional[int] = None
        self._focus_status: Optional[str] = None
        self._obligations: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Breadcrumb
        bc = QHBoxLayout()
        back_btn = QPushButton("\u2190 Back to Overview")
        back_btn.setFlat(True)
        back_btn.clicked.connect(self.back_to_overview.emit)
        bc.addWidget(back_btn)
        self._title = QLabel("Vendor Cores")
        self._title.setStyleSheet("font-size: 16pt; font-weight: bold;")
        bc.addWidget(self._title)
        bc.addStretch(1)
        refresh_btn = QPushButton("\u21bb Refresh")
        refresh_btn.clicked.connect(self.refresh)
        bc.addWidget(refresh_btn)
        history_btn = QPushButton("RGA History\u2026")
        history_btn.clicked.connect(self._show_rga_history)
        bc.addWidget(history_btn)
        root.addLayout(bc)

        # Kanban columns
        cols_row = QHBoxLayout()
        cols_row.setSpacing(8)
        self._columns: dict[str, _Column] = {}
        for status, label in VCO_LIFECYCLE:
            col = _Column(status, label)
            col.card_double_clicked.connect(self._on_card_double_clicked)
            col.context_action_requested.connect(self._on_context_action)
            cols_row.addWidget(col, 1)
            self._columns[status] = col
        root.addLayout(cols_row, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load(
        self,
        *,
        vendor_id: Optional[int],
        focus_status: Optional[str],
    ) -> None:
        self._vendor_id = vendor_id
        self._focus_status = focus_status
        self.refresh()

    def refresh(self) -> None:
        # Title
        vendor_name = ""
        if self._vendor_id:
            try:
                from db.inventory import get_vendor
                v = get_vendor(self._vendor_id) or {}
                vendor_name = str(v.get("name") or "")
            except Exception:
                vendor_name = ""
        if self._vendor_id and vendor_name:
            self._title.setText(f"Vendor Cores · {vendor_name}")
        else:
            self._title.setText("Vendor Cores · All vendors")

        # Pull obligations
        try:
            self._obligations = list_vendor_core_obligations(
                vendor_id=self._vendor_id,
            )
        except Exception as e:
            print(f"[VendorCoresBoard] load failed: {e}")
            self._obligations = []

        # Bucket by folded status
        buckets: dict[str, list[dict]] = {s: [] for s, _ in VCO_LIFECYCLE}
        for o in self._obligations:
            s = fold_status(o.get("status") or "")
            if s in buckets:
                buckets[s].append(o)

        for status, _label in VCO_LIFECYCLE:
            self._columns[status].set_cards(buckets.get(status, []))

    # ------------------------------------------------------------------
    # Context-menu action dispatch
    # ------------------------------------------------------------------
    def _on_card_double_clicked(self, obligation_id: int) -> None:
        """Double-click opens the obligation detail (placeholder shows tooltip)."""
        obl = self._find(obligation_id)
        if not obl:
            return
        QMessageBox.information(
            self, f"Obligation #{obligation_id}",
            "\n".join([
                f"Status: {obl.get('status')}",
                f"Product: {obl.get('product_sku')} — {obl.get('product_title')}",
                f"PO: {obl.get('po_number') or '—'}",
                f"RGA: {obl.get('rga_number') or '—'}",
                f"Deposit: ${float(obl.get('deposit_each') or 0):,.2f} × "
                f"{int(obl.get('units') or 1)}",
                f"Due: {obl.get('due_date') or '—'}",
            ]),
        )

    def _on_context_action(self, action: str, obligation_id: int) -> None:
        obl = self._find(obligation_id)
        if not obl:
            return
        if action == "cancel":
            self._do_cancel(obl)
        elif action == "write_off":
            self._do_write_off(obl)
        elif action == "mark_received":
            self._do_mark_received(obl)
        elif action == "pack_and_ship":
            self._do_pack_and_ship(obl)
        elif action == "credit_received":
            self._do_credit_received(obl)
        elif action in {"reassign", "view"}:
            QMessageBox.information(
                self, action.replace("_", " ").title(),
                "Not implemented yet \u2014 Phase G follow-up.",
            )

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------
    def _do_cancel(self, obl: dict) -> None:
        reason, _ok = _ask_text(self, "Cancel obligation", "Reason (optional):")
        if _ok is False:
            return
        transition_to_cancelled(int(obl.get("id") or 0), reason=reason or "")
        self.refresh()

    def _do_write_off(self, obl: dict) -> None:
        reason, ok = _ask_text(self, "Write off", "Reason for write-off:")
        if not ok:
            return
        transition_to_write_off(int(obl.get("id") or 0), reason=reason or "")
        self.refresh()

    def _do_mark_received(self, obl: dict) -> None:
        # Awaiting customer core → Pending Ship to Vendor. Without a
        # core_return_id wired in from a customer return, we update the
        # obligation directly so the Board action is still useful for
        # manual workflows.
        from db.core_handling import update_vendor_core_obligation
        update_vendor_core_obligation(
            int(obl.get("id") or 0),
            status="pending_vendor_ship",
            notes=" | Customer core received (manual mark via Board)",
        )
        self.refresh()

    def _do_pack_and_ship(self, obl: dict) -> None:
        # Find or create the RGA grouping this obligation needs to live
        # under. Many obligations from the same vendor can share one
        # RGA / packing slip, so we ask the user.
        from .pack_and_ship_dialog import PackAndShipDialog
        dlg = PackAndShipDialog(self, obligation=obl)
        if dlg.exec():
            self.refresh()

    def _do_credit_received(self, obl: dict) -> None:
        rga_id = int(obl.get("rga_id") or 0)
        if not rga_id:
            QMessageBox.warning(
                self, "No RGA",
                "This shipped obligation isn't attached to an RGA. "
                "Open the Pack & Ship dialog from Pending Ship to wire one up.",
            )
            return
        credit_no, ok = _ask_text(self, "Mark credit received",
                                  "Vendor credit / memo number:")
        if not ok:
            return
        transition_to_credit_received(rga_id, vendor_credit_number=credit_no or None)
        self.refresh()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _find(self, obligation_id: int) -> Optional[dict]:
        for o in self._obligations:
            if int(o.get("id") or 0) == int(obligation_id):
                return o
        return None

    def _show_rga_history(self) -> None:
        QMessageBox.information(
            self, "RGA History",
            "Not implemented yet \u2014 Phase G follow-up.",
        )


def _ask_text(parent: QWidget, title: str, prompt: str) -> tuple[str, bool]:
    """Tiny wrapper around QInputDialog so callers don't pollute imports."""
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, title, prompt)
    return text, ok
